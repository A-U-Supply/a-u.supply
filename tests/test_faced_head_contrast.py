"""Faced-slot head contrast — the guard the design lint can't be.

`.slot__head` sets its own colour for every face treatment, but `.action-btn`
declares `color: var(--color-text)` over in ``admin.css``, and an explicit
declaration doesn't inherit. So the head's action buttons rendered
theme-coloured on a darkened image — dark on dark, all six equally. It was
reported four separate times before it was fixed.

**Why this isn't in ``lint-design.mjs``.** That script catches mistakes that
are a *string in a file*: a raw hex, an ad-hoc z-index, a hand-built
thumbnail URL. This one is a *relationship*. ``admin.css`` has no idea
``.action-btn`` will ever render inside a faced head, and ``LatentSlots.svelte``
never mentions ``.action-btn``'s colour. Neither file is wrong on its own —
only the computed style of the rendered pair is. That needs a browser.

**This does not run in CI** (``.github/workflows/deploy.yml`` only pushes to
Dokku — nothing runs pytest). It skips unless you've got the local loop up,
so it costs nothing by default and is here to be run deliberately when you
touch faces:

    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_faced_head_contrast.py -v

It needs the ``viewer-audit-0001`` fixture latent with at least one image
attached and at least one slot. It restores the slot's style when done.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "browser" / "faced_head_contrast.mjs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _up(url: str) -> bool:
    try:
        with urlopen(url, timeout=1.5):
            return True
    except (URLError, OSError):
        return False


requires_browser_loop = pytest.mark.skipif(
    shutil.which("node") is None
    or not os.path.exists(CHROME)
    or not _up("http://localhost:4321")
    or not _up("http://localhost:5000/api/csrf"),
    reason=(
        "needs node, Chrome, and the local loop (astro :4321 + uvicorn :5000). "
        "See this module's docstring."
    ),
)


@requires_browser_loop
def test_faced_head_buttons_are_readable() -> None:
    proc = subprocess.run(
        ["node", str(HARNESS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    # The harness prints one JSON line last; anything else on stdout is noise
    # from Chrome and shouldn't be mistaken for a parse failure.
    line = next(
        (
            ln
            for ln in reversed(proc.stdout.strip().splitlines())
            if ln.startswith("{")
        ),
        None,
    )
    if line is None:
        pytest.fail(
            f"harness produced no JSON.\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )

    payload = json.loads(line)
    if payload.get("error"):
        pytest.skip(f"harness could not start: {payload['error']}")

    results = payload.get("results") or []
    assert results, "harness ran but asserted nothing"

    failed = [r for r in results if not r["pass"]]
    if failed:
        detail = "\n".join(f"  {r['name']} — {r['detail']}" for r in failed)
        pytest.fail(
            f"{len(failed)} of {len(results)} checks failed:\n{detail}\n\n"
            "A faced head owns its text colour; anything inside it must ride "
            "`inherit` / `currentColor`, or `--color-danger-on-overlay` for "
            "destructive actions. A fixed theme token can't be right — the "
            "ground is an arbitrary photo."
        )
