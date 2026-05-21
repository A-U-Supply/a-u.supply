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


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed; skipping design lint"
)
def test_admin_pages_use_design_tokens() -> None:
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
        findings = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"lint-design.mjs did not return valid JSON.\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
    if findings:
        formatted = "\n".join(
            f"  {f['file']}:{f['line']}  {f['kind']}={f['match']}\n    {f['source']}"
            for f in findings
        )
        pytest.fail(
            f"{len(findings)} hardcoded color(s) in admin pages. Replace with "
            f"design tokens — see docs/frontend.md.\n{formatted}"
        )
