"""Design-lint smoke test.

Runs ``node scripts/lint-design.mjs --json`` over the repo and asserts
that the admin pages don't introduce hardcoded colors. The lint scope is
documented at the top of the script.

If this test fails, the failing files / line numbers are in the JSON
output. Fix by replacing the literal with a ``var(--color-*)`` /
``var(--color-status-*)`` / ``var(--color-overlay*)`` token. See the
"Status colors" + "New admin page checklist" sections in
``docs/frontend.md``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "lint-design.mjs"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed; skipping design lint"
)


def _run_lint() -> list[dict]:
    result = subprocess.run(
        ["node", str(SCRIPT), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Internal-error exit code = something went wrong in the script itself,
    # not a finding. Surface that distinctly.
    assert result.returncode != 2, (
        f"lint-design.mjs crashed:\nstderr={result.stderr}\nstdout={result.stdout}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"lint-design.mjs did not return valid JSON.\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )


@requires_node
def test_admin_pages_use_design_tokens() -> None:
    findings = _run_lint()
    if findings:
        formatted = "\n".join(
            f"  {f['file']}:{f['line']}  {f['kind']}={f['match']}\n    {f['source']}"
            for f in findings
        )
        pytest.fail(
            f"{len(findings)} design-lint finding(s). Replace hardcoded colors "
            f"with tokens and ad-hoc z-index values with the scale in "
            f"src/styles/tailwind.css — see docs/frontend.md.\n{formatted}"
        )


@requires_node
def test_zscale_pass_actually_fires() -> None:
    """Prove the z-index guard can fail.

    The clean run above passes whether the rule works or is a no-op, so it
    proves nothing on its own — the same way the 2026-07-26 assertion on
    --z-player passed against a tree-shaken empty string. Drop a file with
    an ad-hoc z-index into the linted tree and assert it gets caught, along
    with the two things it must NOT catch: a value under the floor, and a
    comment that merely mentions one.
    """
    probe = REPO_ROOT / "src" / "styles" / "__zscale_probe.css"
    probe.write_text(
        ".probe { position: fixed; z-index: 1500; }\n"
        "/* prose mentioning z-index: 9999 is not a violation */\n"
        ".local-layering { z-index: 4; }\n"
    )
    try:
        findings = _run_lint()
    finally:
        probe.unlink()

    probe_hits = [f for f in findings if f["file"].endswith("__zscale_probe.css")]
    assert [(f["line"], f["match"]) for f in probe_hits] == [(1, "z-index: 1500")], (
        f"expected exactly the line-1 violation, got {probe_hits}"
    )
