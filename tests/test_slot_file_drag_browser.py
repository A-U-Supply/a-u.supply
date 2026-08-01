"""Dragging a file between slot cards — the part only a browser can answer.

``tests/test_latents_api.py::TestMoveItemBetweenSlots`` covers what the
endpoint does: the row lands at the end of its new slot, the old slot's pin is
dropped, the destination's pin is left alone, siblings keep their order. None
of that says whether you can *reach* the destination.

**The feature exists because slot cards render collapsed.** A card's file list
only exists when the card is open, on the Files tab, and already holding
something — so for most cards, most of the time, a shared SortableJS group has
nothing to drop into. ``.slot__dropzone`` covers the whole card to fix exactly
that, and its regression is silent: the feature still works in the one
arrangement a developer happens to have open while testing by hand, and every
static check stays green. So the load-bearing assertion here drops a row onto a
card that is **shut**.

Also covered, each because it fails quietly:

* **The no-op.** Dropping a row back on its own card must change nothing. A
  short, sloppy drag produces this constantly; ``put`` refuses the source card
  from the DOM rather than from component state, so a stale piece of state
  can't let one through.
* **The pin.** ``SlotPrimaryPin`` is keyed ``(slot_id, media_type)``, so a
  pinned file that leaves would strand a thumbnail on the old card pointing at
  something it no longer holds.
* **Persistence.** A version that only reorders the DOM looks identical until
  you reload.
* **Arrival, on screen.** So does a version that only reaches the *server* —
  and that is how the feature shipped broken. The first cut of this suite
  confirmed the destination with ``serverItems()`` for the drag path and only
  that way for the menu path, so it asserted the API where the rendered card
  was the product. Every move check now asks the DOM before any navigation.
* **The menu path, at both widths.** Dragging is a desktop gesture — a phone
  shows one collapsed tab at a time — so ``Move to slot ▸`` is the only route
  on a phone, the only keyboard route, and the only way anyone discovers the
  feature at all. ``RowActions`` renders a *different* presentation below 640px
  (inline accordion vs the portaled panel), so exercising it on desktop says
  nothing about the case it exists for. The phone pass also checks the nested
  rows are still 44px targets — an indent that shrank them would be a
  regression no assertion about the move itself would catch.

**Driving a real drag needs CDP drag interception.** SortableJS uses native
HTML5 drag-and-drop here (``DRAG_OPTS`` sets no ``forceFallback``), which
synthetic mouse events cannot start: ``Input.setInterceptDrags`` plus replaying
the ``Input.dragIntercepted`` payload through ``Input.dispatchDragEvent`` is
the only way. The emulated viewport is deliberately tall — a mouse event
dispatched past the viewport edge lands nowhere and the drag simply never
starts, with no error anywhere to explain it.

**Proved able to fail.** Rendering ``.slot__dropzone`` only for *open* cards
turns exactly four checks red — the row never leaves, the server never sees it
in the destination, it doesn't survive a reload, and the pin outlives the file
— while the menu path stays green, because it doesn't go through the dropzone.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up:

    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_slot_file_drag_browser.py -v

Self-cleaning: creates its own ``zz-slotdrag`` latent and files, removes both.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "slot_file_drag.mjs"
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
def test_a_file_can_be_dragged_onto_a_collapsed_slot() -> None:
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
            "`.slot__dropzone` must cover the WHOLE card and must exist even "
            "when the card is collapsed — that is the only reason a shut slot "
            "is reachable, and a card's file list doesn't exist unless it is "
            "open, on the Files tab, and already holding something. It must "
            "also stay inert on the card the drag started from, or a short "
            "drag 'moves' a file into the slot it already lives in."
        )
