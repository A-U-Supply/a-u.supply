// Shared triage logic for the AI issue triage workflows.
// Used by .github/workflows/triage.yml (on issue open) and triage-backfill.yml (manual sweep).

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
    'Step 1 — Apply every label from this list that genuinely fits. Use one type label (bug / enhancement / documentation / question / chore — pick the best one), every area: label that applies (an issue can touch multiple areas), and a priority: label only if you can clearly justify it. Add good first issue / help wanted only if obviously applicable. Do not pad. Do not invent labels — only use these verbatim:',
    AVAILABLE_LABELS.join(', '),
    '',
    'Step 2 — Rewrite the body in clean GitHub-flavored markdown matching the project\'s issue templates. Choose the shape based on the inferred type:',
    '',
    '- bug → ## Summary, ## Steps to reproduce, ## Expected behavior, ## Actual behavior, ## Environment, ## Logs / screenshots, ## Additional context',
    '- enhancement → ## Problem, ## Proposed solution, ## Alternatives considered, ## Additional context',
    '- question → ## What I\'m trying to do, ## What I\'ve tried, ## What I\'ve looked at, ## Additional context',
    '- chore → ## What, ## Why, ## Notes / risks',
    '- documentation → use the question shape unless the body clearly fits one of the others',
    '',
    'Rules for the rewrite:',
    '- Preserve every concrete detail (file paths, error messages, versions, URLs, codes, names).',
    '- Do NOT invent facts. Reorganize and clarify only.',
    '- If a section has no information, write "_Not provided_" rather than guessing.',
    '- Keep prose tight. Use bullets / numbered lists where it helps.',
    '- Do not include the original raw body — it will be appended automatically.',
    '',
    'Respond with ONLY a JSON object — no prose, no code fences:',
    '{"labels": ["..."], "rewritten_body": "..."}',
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

module.exports = async ({ github, context, core, issue, rewriteBody }) => {
  const title = issue.title || '';
  const originalBody = issue.body || '';
  const issueNumber = issue.number;

  const parsed = await callModel(buildPrompt(title, originalBody));

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

  if (rewriteBody && parsed.rewritten_body) {
    const newBody = originalBody.trim()
      ? `${parsed.rewritten_body}\n\n---\n*Original:*\n\n${originalBody}`
      : parsed.rewritten_body;
    await github.rest.issues.update({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: issueNumber,
      body: newBody,
    });
    core.info(`#${issueNumber} body rewritten`);
  }
};

module.exports.AVAILABLE_LABELS = AVAILABLE_LABELS;
