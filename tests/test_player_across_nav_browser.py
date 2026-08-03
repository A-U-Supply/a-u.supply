"""The comment window must still clear the player after an in-app navigation.

Reported from a phone on 2026-08-02: "backed out of the latent with the player
still up… when it came back up it was partially behind the player."

**What a navigation takes with it.** Astro's ClientRouter calls
``swapRootAttributes()``, which removes *every* attribute from ``<html>`` (only
``data-astro-transition*`` survive, so an inline custom property does not), and
``swapBodyElement()``, which replaces ``<body>`` and its classes. The player is
``transition:persist``, so it sails through untouched: no effect re-runs, and
its ``ResizeObserver`` never fires, because the bar never changed size — only
the document around it did.

So ``--player-h`` and ``body.player-active`` are simply gone, and
``.marginalia``'s ``bottom: var(--player-h, 72px)`` falls back to a number from
before the phone breakpoint existed. Measured at 390px: the bar is 165px, and
the comment window lands **93px behind it**. The same wipe puts the video PiP
back under the transport (the bug #592 fixed) and stops the page's bottom
padding clearing the bar.

Nothing static can see it — the CSS is right, the measurement is right, and
both are right again after a reload. Only the swap is wrong, and only while the
player is up.

The fix routes both publishers through ``src/lib/documentState.ts``, which
re-applies after every ``astro:after-swap``. ``scripts/lint-design.mjs`` refuses
a direct write to ``documentElement.style`` or ``document.body.classList`` from
a component so the next persistent island can't reintroduce it.

The load-bearing assertion is **geometric** — the comment window's bottom edge
against the player's top edge — so a future fix that drops the custom property
altogether still passes. The variable is asserted separately because it is what
regresses first.

**Proved able to fail.** With the re-publish removed, four checks go red: the
overlap returns at exactly 93px, ``--player-h`` comes back empty, and
``body.player-active`` is gone.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up::

    SEARCH_MEDIA_DIR=$PWD/data/search-media PYTHONPATH=$PWD \\
        ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_player_across_nav_browser.py -v

Self-cleaning: creates its own ``zz-playernav`` latent and file, removes both.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "player_across_nav.mjs"
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
def test_the_comment_window_clears_the_player_after_navigating() -> None:
    proc = subprocess.run(
        ["node", str(HARNESS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    line = next(
        (ln for ln in reversed(proc.stdout.strip().splitlines()) if ln.startswith("{")),
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
            "A ClientRouter navigation strips every attribute off <html> and "
            "replaces <body>, while a transition:persist island survives them "
            "both — so anything it published about itself is gone, with no "
            "effect re-run and no ResizeObserver callback to put it back. "
            "Publish through src/lib/documentState.ts, which re-applies after "
            "astro:after-swap."
        )
