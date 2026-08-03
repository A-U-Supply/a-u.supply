"""Rearranging songs inside a slot must not hang the tab.

Reported by David on 2026-08-02: "when rearranging songs within a slot, the
page often freezes and throws a page unresponsive error." The second drag
inside one slot did it every time, and the first already rendered an order
nobody asked for.

**Nothing but a browser can see it.** SortableJS rearranges the DOM as you
drag; the file list is a Svelte keyed ``{#each}`` whose body is a row ``<li>``
*and* the ``{#if}`` for a session's extracted children, so Svelte tracks each
item as a *range* of nodes — the row plus the anchor comment after it.
Sortable moves the ``<li>`` alone. The range breaks, and the next update walks
forward from the row looking for an end node that now sits behind it: ``move()``
in ``svelte/internal/client/dom/blocks/each.js`` never arrives and cycles the
nodes ahead of its destination for ever. Chrome puts up "Page Unresponsive".

Nothing throws and nothing logs. The API is right the whole time — every
reorder reached the server correctly, including the ones the screen got wrong
— so an assertion against the endpoint (``test_latents_api.py`` has several)
passes through this bug without noticing. ``tests/test_slot_file_drag_browser.py``
drives real drags and also missed it, because it never does a *second* drag in
the same list: one drag corrupts the node range, the next one walks it.

The fix is ``createSortable()`` in ``src/lib/dragOptions.ts`` — Sortable's DOM
changes are undone at the drop and the state assignment moves the row instead.
``scripts/lint-design.mjs`` refuses a raw ``Sortable.create`` outside that
module so the next list can't reintroduce it.

**Proved able to fail.** Against the pre-fix component this goes red on four
checks: the screen disagrees with the server after the first drag, the page
stops answering after the second, and the two checks past it can't run.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up::

    SEARCH_MEDIA_DIR=$PWD/data/search-media PYTHONPATH=$PWD \\
        ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_slot_reorder_freeze_browser.py -v

Self-cleaning: creates its own ``zz-reorderfreeze`` latent and files, removes
both — through node's own fetch rather than the page, since the failure this
test exists for leaves the page unable to answer anything.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "slot_reorder_freeze.mjs"
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
def test_reordering_songs_in_a_slot_does_not_freeze_the_page() -> None:
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
            "Sortable's DOM changes must not outlive the drop. Every one of "
            "these lists is a Svelte keyed {#each}, and Svelte tracks an item "
            "as a RANGE of nodes — a row plus the {#if} anchor after it. Move "
            "the row on its own and the next update walks that range for ever, "
            "which is the frozen tab. Build the list with createSortable() "
            "from src/lib/dragOptions.ts: it puts the DOM back and lets the "
            "state assignment move the row."
        )
