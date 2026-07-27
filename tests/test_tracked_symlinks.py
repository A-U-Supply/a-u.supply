"""No tracked symlink may point outside the repo.

A `node_modules` symlink — the trick that gives a git worktree the main
checkout's dependencies — was committed on 2026-07-25 (ffc26d5). Its target
was an absolute path to the main checkout's *own* `node_modules`, so checking
that commit out in the main checkout replaced the real directory with a
symlink to itself. Every `npm`/`astro` command then failed with "Too many
levels of symbolic links" until the tree was reinstalled.

`.gitignore` said `node_modules/`, which matches a directory only — the
symlink is a file, so it was never ignored and one `git add` swept it in.
That hole is closed (no trailing slash), but the class is broader than one
name: any absolute-path symlink is machine-specific and breaks for everyone
who isn't the person who committed it.

Relative symlinks that stay inside the repo are fine and are left alone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# git's mode for a symlink blob.
SYMLINK_MODE = "120000"


def _tracked_symlinks() -> list[tuple[str, str]]:
    """Return (path, target) for every symlink in the index."""
    listing = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    out: list[tuple[str, str]] = []
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if not parts or parts[0] != SYMLINK_MODE:
            continue
        # The blob's contents ARE the link target.
        target = subprocess.run(
            ["git", "cat-file", "-p", parts[1]],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        out.append((path, target))
    return out


def test_no_absolute_or_escaping_symlinks() -> None:
    offenders = []
    for path, target in _tracked_symlinks():
        if target.startswith("/") or target.startswith("~"):
            offenders.append(f"  {path} -> {target}  (absolute path)")
            continue
        resolved = (REPO_ROOT / path).parent / target
        try:
            resolved.resolve().relative_to(REPO_ROOT.resolve())
        except ValueError:
            offenders.append(f"  {path} -> {target}  (points outside the repo)")

    assert not offenders, (
        "Tracked symlink(s) point outside the repo. These are machine-specific "
        "and break every other checkout — see the module docstring for how "
        "node_modules did it.\n" + "\n".join(offenders)
    )
