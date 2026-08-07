"""The latent detail page rendering twice — the part only a browser can answer.

Brendan: "every once in a while I open a latent and every section is doubled…
when I refresh the page the latent loads normal." Two copies of Repo, of Links,
of Documents, of the section map — and exactly one header. That asymmetry is
the whole diagnosis: the header is rebuilt with ``innerHTML`` and cannot
double, while every island is ``mount``ed and can.

Two distinct defects, both invisible to the API and to every static check:

* **Two module instances.** ``detail.astro``'s script ships as a
  content-hashed chunk. A tab that has already opened a latent holds chunk H1,
  evaluated, with an ``astro:page-load`` listener and its own ``mounted``.
  Deploy H2 under that open tab and click a latent: ClientRouter swaps in HTML
  pointing at H2, the browser evaluates it as a *second* instance, both mount a
  full set of islands, and neither teardown can reach the other's components —
  ``unmount()`` is keyed by a ``WeakMap`` private to each copy of Svelte. A hard
  refresh loads only H2, which is why refreshing cured it. Re-importing the
  same chunk under a cache-busting query builds a second instance without a
  redeploy, which is how this is tested.
* **Two ``init()`` runs inside one instance.** The module calls ``init()`` as it
  evaluates *and* listens for ``astro:page-load``; on a client-side nav both
  fire, because evaluation happens with a resolved ``__authReady`` left over
  from the previous page. The guard sat before the by-slug lookup and the flag
  was set after it, so whether the second run walked through was decided by
  whether ``/api/me`` or ``/api/projects/by-slug`` answered first. This one
  self-repaired in the DOM — the later run unmounted the earlier one's
  components — so its only symptom was the page fetching everything twice.
  That is why it survived three years and two previous fixes to this same
  guard (#279, #281, then #286 reopened it by adding the slug lookup).

**The pretty URL is the one that matters.** ``?id=`` short-circuits
``resolveProjectId`` with no fetch at all, closing the second window entirely,
so a suite using the query-string form would pass straight through the bug.
``/admin/latents/<slug>`` is a ``main.py`` fallback over the built output, so
this runs against ``dist`` rather than the dev server.

**Proved able to fail**, each half separately:

* putting the claim back after the slug lookup turns the two request-count
  checks red (2 lookups, 2 project loads) and nothing else;
* dropping the target-clearing turns the duplicate-instance checks red with
  every island at 2 and ``nameInputs`` at 1 — the reported bug exactly;
* neutering the epoch guard leaves the DOM correct and turns the swap-cycle
  check red at 2 project loads, which is the leak it exists to stop.

**This does not run in CI** (it needs Chrome and a built ``dist``). It skips
unless the loop is up::

    npm run build
    ./.venv/bin/python -m uvicorn main:app --port 5000 &
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_detail_double_mount_browser.py -v

Self-cleaning: creates its own ``zz-dblmount`` latent and removes it.
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
HARNESS = REPO_ROOT / "tests" / "browser" / "detail_double_mount.mjs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BUILT_PAGE = REPO_ROOT / "dist" / "admin" / "latents" / "detail" / "index.html"


def _up(url: str) -> bool:
    try:
        with urlopen(url, timeout=1.5):
            return True
    except (URLError, OSError):
        return False


requires_browser_loop = pytest.mark.skipif(
    shutil.which("node") is None
    or not os.path.exists(CHROME)
    or not BUILT_PAGE.exists()
    or not _up("http://localhost:5000/api/csrf"),
    reason=(
        "needs node, Chrome, a built dist/ and the API on :5000. "
        "See this module's docstring."
    ),
)


@requires_browser_loop
def test_a_latent_renders_exactly_once() -> None:
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
            "Every island count at 2 with nameInputs at 1 means a second module "
            "instance mounted on top of the first — emptying the mount targets "
            "before mounting is what prevents that, and it cannot be replaced by "
            "unmounting `mounted`, which only ever holds one instance's own "
            "components. Doubled request counts instead mean init() re-entered: "
            "the page must be claimed BEFORE the by-slug lookup, never after."
        )
