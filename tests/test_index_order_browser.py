"""Latents index reorder — the three guards that need a rendered page.

``tests/test_latents_api.py::TestLatentOrder`` covers the placement algebra and
the ``updated_at`` regression. What it cannot see is whether the control is
*reachable*, *legible*, or *indicated* — three relationships that live between
files, where every one of them is individually correct:

1. **The grip must win the hit test.** The card is covered by a stretched
   ``.card__link`` so it stays one click target; the grip sits above it. Invert
   the two and the grip still renders, still shows a grab cursor, and simply
   cannot be picked up. Nothing static catches a dead control.
2. **The grip's colour.** It is the only card chrome outside
   ``.card__content``, so it cannot inherit the treatment's text colour — and
   the *top* of a hero card is bare photograph under every treatment, because
   plate's opaque strip is pinned to the bottom. The "child declares its own
   colour on a faced surface" bug has been reported five times in Latents.
3. **The drop indicator.** ``ghostClass: 'card--landing'`` is a string
   agreement between the Sortable options and a CSS rule. Rename either side
   and drags keep working perfectly, with no indicator at all.

**Why not ``lint-design.mjs``.** That script catches a *string in a file* — a
raw hex, an ad-hoc z-index, a hand-built thumbnail URL. These are
relationships: the CSS that declares the grip's colour has no idea what it will
be composited over, and the JS that names ``card--landing`` never mentions CSS.
Only the computed style of the rendered pair is wrong. That needs a browser.

**Each guard is proved able to fail.** The harness re-introduces both bugs —
the grip hardcoding the theme token, and the link painted above the grip — and
asserts the corresponding check flips. A green run that cannot go red is not a
guard.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up, so it costs nothing by default and is here to be
run deliberately when you touch the index grid:

    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_index_order_browser.py -v

It needs no fixture: it creates its own throwaway latents (prefixed
``zz-order-probe``) and deletes them, leaving no residue.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "index_order.mjs"
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
def test_index_grid_can_actually_be_reordered() -> None:
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
            "The grip must sit ABOVE the stretched link (or it cannot be "
            "grabbed), must carry --color-on-overlay plus a backing over "
            "imagery (theme tokens can't be right — the ground is an arbitrary "
            "photo), and `ghostClass` must match the .card--landing rule."
        )
