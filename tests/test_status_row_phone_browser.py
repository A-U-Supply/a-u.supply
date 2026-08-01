"""The latent detail status row on a phone — the part only a browser can answer.

There are five statuses (forming, developing, fixing, shipped, abandoned), and
``.status-row`` is a single flex item inside a container that wraps its *items*
rather than their contents. With no ``flex-wrap`` of its own, the row's minimum
width is all five buttons unbroken, so it runs off the right edge: ``shipped``
clipped at the viewport, ``abandoned`` off-screen entirely. A latent could not
be marked abandoned from a phone at all.

**Reachability is the assertion, not overflow**, and the distinction is the
whole reason this file exists:

* ``document.documentElement.scrollWidth`` never grows — the overflow is clipped
  further up the tree. The ``no horizontal overflow at 390px`` checks in
  ``tests/browser/slot_file_drag.mjs`` and ``tests/browser/section_arrange.mjs``
  therefore stayed green through the entire bug. Measured with the fix reverted,
  not assumed.
* A button inside the viewport can still be unusable, so each is hit-tested with
  ``elementFromPoint`` at its own centre, and the last one — ``abandoned``, the
  one a clipped row loses — is clicked, with the new status read back from the
  server. That is the difference between "it is laid out" and "you can mark this
  abandoned".

320px is deliberate: five chips cannot fit one line there at a legible size and
are not meant to. That width asserts every button is still reachable while
expecting more than one line — the wrap is the safety net, the media query is
the plan. A legibility floor guards the other direction, since shrinking type
until it fits is not a fix if nobody can read the result.

**Proved able to fail.** Restoring ``flex-wrap: nowrap`` on ``.status-row``
turns the 320px reachability and line-count checks red; dropping the media query
as well takes 390px and 360px with them.

**This does not run in CI** (it needs Chrome plus both dev servers). It skips
unless the local loop is up::

    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    npx astro dev --port 4321 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_status_row_phone_browser.py -v

Self-cleaning: creates its own ``zz-statusrow`` latent and removes it.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "status_row_phone.mjs"
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
def test_every_status_is_reachable_on_a_phone() -> None:
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
            "`.status-row` must keep `flex-wrap: wrap` so the row breaks instead "
            "of clipping — that is what makes a sixth status safe to add. The "
            "media query is what keeps it on one line at ordinary phone widths; "
            "condensing further to avoid a wrap is the wrong trade once the "
            "labels stop being readable."
        )
