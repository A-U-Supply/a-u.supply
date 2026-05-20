// Shared triage logic for the AI issue triage workflows.
// Used by .github/workflows/triage.yml (on issue open) and triage-backfill.yml (manual sweep).
// Every triaged issue ends up with: canonical labels, canonical title, structured body.

const AVAILABLE_LABELS = [
  'bug',
  'enhancement',
  'documentation',
  'question',
  'chore',
  'duplicate',
  'invalid',
  'wontfix',
  'good first issue',
  'help wanted',
  'area:admin',
  'area:frontend',
  'area:player',
  'area:catalog',
  'area:backend',
  'area:deploy',
  'area:worker',
  'area:auth',
  'priority:critical',
  'priority:high',
  'priority:low',
];

function buildPrompt(title, body) {
  return [
    'You are triaging a GitHub issue for the a-u.supply project (Astro frontend + FastAPI backend, music/art site, deployed to Dokku).',
    '',
    `Title: ${title}`,
    '',
    'Body:',
    body || '(no body)',
    '',
    'Produce three things so every issue reads consistently across the repo.',
    '',
    '1) Title — rewrite into: <type>(<scope>): <description>',
    "   - type: one of feat | fix | docs | chore | question (enhancement → feat, bug → fix, documentation → docs)",
    '   - scope: short kebab-case noun. Prefer one of admin, frontend, player, catalog, backend, deploy, worker, auth. Use a more specific scope (e.g. search, upload, latents, bots) when it adds clarity. Omit "(scope)" entirely and use "<type>: <description>" only when nothing fits.',
    '   - description: imperative mood, lowercase first character (unless a proper noun starts it), no trailing period',
    '   - Total title <= 80 characters',
    '   - If the existing title is ALREADY in this exact canonical form, return it unchanged',
    '',
    '2) Labels — apply every label from this list that genuinely fits. Use exactly one type label (bug / enhancement / documentation / question / chore), every area: label that applies (an issue can touch multiple), and a priority: label only when clearly justified. Add good first issue / help wanted only if obviously applicable. Do not pad. Available labels (use verbatim, no others):',
    AVAILABLE_LABELS.join(', '),
    '',
    "3) Body — rewrite in clean GitHub-flavored markdown matching the section structure for the inferred type:",
    '   - bug → ## Summary, ## Steps to reproduce, ## Expected behavior, ## Actual behavior, ## Environment, ## Logs / screenshots, ## Additional context',
    '   - enhancement → ## Problem, ## Proposed solution, ## Alternatives considered, ## Additional context',
    "   - question → ## What I'm trying to do, ## What I've tried, ## What I've looked at, ## Additional context",
    '   - chore → ## What, ## Why, ## Notes / risks',
    '   - documentation → use the question shape unless the body clearly fits one of the others',
    '',
    'Rules for the body rewrite:',
    '- Preserve every concrete detail (file paths, error messages, versions, URLs, codes, names, usernames)',
    '- Do NOT invent facts. Reorganize and clarify only',
    '- If a section has no information, write "_Not provided_" rather than guessing',
    '- Keep prose tight. Use bullets / numbered lists where it helps',
    '- Do not include the original raw body — it will be appended automatically',
    '- If the body is empty or near-empty, write a brief placeholder body that lays out the section skeleton with "_Not provided_" so a human can fill it in',
    '',
    'Respond with ONLY a JSON object — no prose, no code fences:',
    '{"title": "...", "labels": ["..."], "rewritten_body": "..."}',
  ].join('\n');
}

async function callModel(prompt) {
  const maxAttempts = 6;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const resp = await fetch('https://models.github.ai/inference/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({
        model: 'openai/gpt-4o-mini',
        messages: [{ role: 'user', content: prompt }],
        response_format: { type: 'json_object' },
        temperature: 0.2,
      }),
    });

    if (resp.ok) {
      const data = await resp.json();
      const content = data?.choices?.[0]?.message?.content;
      if (!content) {
        throw new Error(`No content in model response: ${JSON.stringify(data)}`);
      }
      return JSON.parse(content);
    }

    const errBody = await resp.text();
    const retriable = resp.status === 429 || resp.status >= 500;
    if (!retriable || attempt >= maxAttempts) {
      throw new Error(`Models API ${resp.status} after ${attempt} attempt(s): ${errBody}`);
    }

    const retryAfterMs = parseInt(resp.headers.get('retry-after') || '0', 10) * 1000;
    const backoff = Math.min(10000 * 2 ** (attempt - 1), 60000);
    const sleepMs = Math.max(backoff, retryAfterMs);
    console.log(
      `Models API ${resp.status}, retrying in ${sleepMs}ms (attempt ${attempt}/${maxAttempts})`
    );
    await new Promise((r) => setTimeout(r, sleepMs));
  }
  throw new Error('callModel: exhausted retry loop without returning');
}

const ORIGINAL_MARKER = '\n\n---\n*Original:*\n\n';

// If we've triaged this issue before, the body looks like:
//   <rewritten>\n\n---\n*Original:*\n\n<original>
// Re-runs operate on the true original so triage is idempotent rather than
// nesting deeper each time.
function unwrapOriginal(body) {
  if (!body) return '';
  const idx = body.indexOf(ORIGINAL_MARKER);
  return idx === -1 ? body : body.slice(idx + ORIGINAL_MARKER.length);
}

module.exports = async ({ github, context, core, issue }) => {
  const currentTitle = issue.title || '';
  const originalBody = unwrapOriginal(issue.body || '');
  const issueNumber = issue.number;

  const parsed = await callModel(buildPrompt(currentTitle, originalBody));

  // Labels — additive
  const chosen = Array.isArray(parsed.labels)
    ? Array.from(new Set(parsed.labels.filter((l) => AVAILABLE_LABELS.includes(l))))
    : [];

  if (chosen.length > 0) {
    await github.rest.issues.addLabels({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: issueNumber,
      labels: chosen,
    });
    core.info(`#${issueNumber} labels added: ${chosen.join(', ')}`);
  } else {
    core.info(`#${issueNumber} no valid labels chosen`);
  }

  // Title + body — always rewrite, both for new issues and for backfill sweeps
  const update = {};
  if (typeof parsed.title === 'string' && parsed.title.trim() && parsed.title !== currentTitle) {
    update.title = parsed.title.trim();
  }
  if (typeof parsed.rewritten_body === 'string' && parsed.rewritten_body.trim()) {
    update.body = originalBody.trim()
      ? `${parsed.rewritten_body}${ORIGINAL_MARKER}${originalBody}`
      : parsed.rewritten_body;
  }

  if (Object.keys(update).length > 0) {
    await github.rest.issues.update({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: issueNumber,
      ...update,
    });
    const parts = [];
    if (update.title) parts.push('title');
    if (update.body) parts.push('body');
    core.info(`#${issueNumber} updated: ${parts.join(' + ')}`);
  } else {
    core.info(`#${issueNumber} already canonical, no update needed`);
  }
};

module.exports.AVAILABLE_LABELS = AVAILABLE_LABELS;
