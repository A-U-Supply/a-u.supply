"""Arranging the latent detail page's sections — the part only a browser can answer.

``tests/test_latents_api.py::TestSectionLayout`` covers the grammar: what a
valid ``section_layout`` is, that a write replaces rather than merges, that
``{}`` clears the column back to NULL. None of that says whether the page
actually rearranges, or — the thing this feature promises — whether hiding a
section leaves its content alone.

So the load-bearing assertion writes a paragraph into Documents, hides
Documents, reloads, shows it again, and compares the textarea character for
character. A version that unmounted the island, or cleared anything
server-side, passes every other check here and fails that one.

Also covered, each because it fails quietly:

* **The chip.** A hidden section's map chip has to go, or clicking it scrolls
  to something that isn't on the page. That is also why the head states a
  hidden count: once the chip is gone, ``Arrange`` is the only way back, so the
  page has to say that something is missing.
* **Persistence.** The layout is a PATCH plus a broadcast, and a version that
  only repaints looks identical until you reload.
* **Order is CSS ``order``, not DOM position.** ``.islands`` is a flex column,
  so sections are re-ranked without moving nodes — which is what keeps the
  islands mounted. Assertions therefore sort by on-screen geometry; comparing
  ``children`` would answer a different question and pass either way.
* **Slots cannot leave their block.** The nested list is a separate Sortable
  with **no shared** ``group``, and that is the *only* thing holding the line:
  ``draggable: '.arrange-row'`` on the outer list filters what that list can
  originate, not what can be put into it, so a shared group lets a slot land
  among the sections with the selector still in place. Measured, not reasoned
  — adding a group is exactly the convenience a later refactor reaches for.

* **The phone.** The arrows are the mobile path and the keyboard path, and the
  rows have to stay 44px targets in a dialog that is mostly controls.

**Proved able to fail**, twice:

* Making ``applySectionLayout`` a no-op turns **six** checks red — the section
  doesn't leave the page, it isn't still hidden after a reload, the arrows move
  nothing at either width, the drag moves nothing, and the map chips stop
  tracking the page order. Everything else stays green, which is the point of
  listing them: the resolver is a pure function, the dialog still renders, the
  chip still disappears (that is map-side), and the layout still reaches the
  server. None of those is evidence that a reader sees anything different.
* Giving the two lists a shared Sortable ``group`` turns the escape check red
  with ``1 slot row(s) escaped the block``.

The escape check also records **whether the drag actually started**, separately
from its outcome. "Nothing moved" is only evidence if a gesture happened, and a
drag that silently fails to start is the most likely way for that check to rot
into a vacuous pass — which is precisely what an earlier version of it did.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up::

    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_section_arrange_browser.py -v

Self-cleaning: creates its own ``zz-arrange`` latent, slots and document, and
removes them.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "section_arrange.mjs"
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
def test_sections_can_be_hidden_and_reordered_without_losing_content() -> None:
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
            "Hiding a section is presentation only: the island stays mounted "
            "with its data loaded and merely stops being painted, so anything "
            "written in it must come back untouched. Order is CSS `order` on "
            "the `.islands` flex column for the same reason — moving nodes "
            "between parents would remount the islands and throw their state "
            "away. And the slots list is a separate Sortable with no shared "
            "group, which is what keeps a slot inside the slots block."
        )
