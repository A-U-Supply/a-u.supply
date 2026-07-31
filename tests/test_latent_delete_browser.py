"""Deleting a latent — the guards that need a rendered page.

``tests/test_latents_api.py::TestLatentDelete`` covers what the endpoint does:
the files survive, the structure cascades, the threads don't, the neighbours
keep their dates. What it cannot see is the part an admin actually touches, and
every piece of that is a relationship between files that are each individually
correct:

1. **The button's ground.** ``.action-btn--danger`` hardcodes a red tuned for a
   light page, over in ``admin.css``. The latent header can carry the latent's
   cover art under a black veil — a *mid*-tone ground in light mode, where that
   red drops under AA. ``--color-danger-on-overlay``, the token that fixed this
   class of bug on the dark slot faces, reads *worse* on a mid ground. Neither
   file is wrong alone. The fix is an opaque plate on the button, so the guard
   is: the button's own background is fully opaque and its ink clears AA
   against it, in both themes. Remove the plate and the artwork shows through —
   fine on a dark photo, unreadable on a pale one, green in every static check.
   This is the sixth reported instance of the class.
2. **The dialog has to be on top.** ``showModal()`` puts it in the browser's
   top layer, which is the whole reason it's a native ``<dialog>``: the header
   sets ``isolation: isolate`` for its backdrop, and a hand-rolled overlay
   inside that stacking context is the trap that cost four separate fixes
   (#573/#574/#575/#581). Rewrite it as a div and it still "opens" — it just
   renders somewhere useless, silently.
3. **The typed-name gate.** One string comparison drives ``disabled``. Break it
   either way and nothing errors: either the gate never opens (dead feature) or
   it never closes (an irreversible action one stray click away).
4. **The path itself.** Nothing else clicks this button end to end — not the
   failure path either, where the dialog must stay open rather than strand the
   admin on a page that may no longer describe anything.

**Why not ``lint-design.mjs``.** That script catches a *string in a file*. None
of the above is a string; they are computed styles, hit tests and state.

**Proved able to fail.** Swapping the button's plate for ``transparent`` flips
both opacity checks red (verified against the pale-artwork case in both
themes). A green run that cannot go red is not a guard.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up, so it costs nothing by default and is here to be
run deliberately when you touch the latent header or the delete path:

    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_latent_delete_browser.py -v

It needs no fixture: it creates its own throwaway latents (prefixed
``zz-delete-probe``) and deletes them, leaving no residue. If the index has any
image in it, the harness borrows one as a cover so the *hard* contrast case is
the one measured; if it doesn't, the check that says so fails rather than
quietly measuring the easy case.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "latent_delete.mjs"
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
def test_a_latent_can_actually_be_deleted() -> None:
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
            "DELETE must keep its own OPAQUE plate — the header's ground is an "
            "arbitrary photo under a black veil, so neither the hardcoded red "
            "nor --color-danger-on-overlay is safe there. The confirmation must "
            "stay a native <dialog> opened with showModal(), or the header's "
            "stacking context swallows it. And the typed-name gate must open "
            "only on a trimmed, case-insensitive match."
        )
