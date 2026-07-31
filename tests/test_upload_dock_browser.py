"""The upload dock — the one guard that needs a real page swap.

``tests/test_search_api.py`` covers what the server does with an upload and
what it says to Slack when one fails. It cannot see the thing this feature
actually is: **an upload that keeps going after you navigate**.

That property comes from **the queue being a module singleton**
(``src/lib/uploadQueue.ts``). Astro's client router swaps the DOM but never
reloads the module registry, so the queue and its ``XMLHttpRequest``s are out
of reach of a page swap. Put that state back inside the component — where it
used to live — and the island dies with the body, the transfer aborts, and the
file silently never arrives. Every static check stays green through that: the
page renders, the dock appears, the button works.

``transition:persist="upload-dock"`` on the wrapper in ``layouts/Admin.astro``
is a *separate*, smaller guarantee: it keeps the dock's DOM node so the bar
doesn't tear down and remount on every navigation, losing its expanded state
and flashing. This was checked by experiment — removing the attribute leaves
every transfer assertion green — which is why there is a distinct
element-identity probe. Two mechanisms, two checks; conflating them would mean
one of the two could regress unnoticed.

So the harness starts a real upload, throttles the uplink so "mid-transfer" is
a fact rather than a race against localhost, clicks a nav link, and then asserts
on the far side: the byte counter is still climbing, the item is on the server
afterwards, and the bar is the same element it was before. It also covers:

* **Geometry.** The dock reads ``--player-h`` — the player's *measured* height,
  published on ``<html>`` and removed when the player goes away — so it rides
  above the bar when there's music and sits on the floor when there isn't. The
  check drives that variable directly in both directions. A hardcoded offset
  passes a screenshot and fails the moment the player wraps to two rows.
* **The real picker path**, through Tribute's file input via
  ``DOM.setFileInputFiles``, so the handoff wiring is covered and not just the
  event contract.
* **Stay-until-dismissed** — the finished bar is the record of what happened
  and must not evaporate on a timer.

**Proved able to fail.** Dropping ``transition:persist`` from the dock's
wrapper turns the element-identity check red and leaves the rest green — which
is what established that the two mechanisms are independent in the first place.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up:

    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_upload_dock_browser.py -v

Self-cleaning: every file it uploads is prefixed ``zz-dock-probe`` and deleted
on the way out.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "upload_dock.mjs"
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
def test_an_upload_survives_navigation() -> None:
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
            "The queue must stay in the src/lib/uploadQueue.ts module, not in "
            "component state — module scope is what the page swap cannot "
            "reach, and moving it back aborts every in-flight upload on the "
            "next link click, silently. The dock must stay wrapped in "
            "transition:persist so the bar isn't torn down and remounted on "
            "every navigation. Its offset must stay "
            "bottom: var(--player-h, 0px) so it tracks the player's MEASURED "
            "height rather than a constant that drifts when the bar wraps."
        )
