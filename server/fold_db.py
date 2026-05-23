"""Read-only access to the Fold (Lemmy) Postgres database.

Lemmy stores everything in its own Postgres. We attach to it as a *separate*
SQLAlchemy engine via the `FOLD_DATABASE_URL` env var and run raw SQL for
all reads — no ORM models. Lemmy's schema shifts between point releases
(0.19.x → 1.0 in particular) and raw SQL keeps the diff small and obvious
when that day comes.

When `FOLD_DATABASE_URL` is unset, `is_configured()` returns False and every
notification source that depends on Fold gracefully no-ops. This keeps local
dev and any deploy that hasn't been linked yet bootable.

On Dokku, the linkage is established with:

    dokku postgres:link <fold-postgres-svc> a-u-supply --alias FOLD_DATABASE

…which exports `FOLD_DATABASE_URL` into the app. (Plain `postgres:link`
without `--alias` would clobber `DATABASE_URL`, which a-u.supply doesn't
use — we're on SQLite — but the alias keeps intent legible.)
"""

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine


FOLD_DATABASE_URL = os.environ.get("FOLD_DATABASE_URL", "").strip()

_engine: Engine | None = None


def is_configured() -> bool:
    return bool(FOLD_DATABASE_URL)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        if not FOLD_DATABASE_URL:
            raise RuntimeError("FOLD_DATABASE_URL is not configured")
        _engine = create_engine(
            FOLD_DATABASE_URL.replace("postgres://", "postgresql://"),
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
        )
    return _engine


@contextmanager
def fold_connection() -> Iterator[Connection]:
    """Yield a read-only Connection for Lemmy queries.

    Raises RuntimeError if `FOLD_DATABASE_URL` is unset — callers must
    check `is_configured()` first or be prepared to catch.
    """
    eng = get_engine()
    with eng.connect() as conn:
        yield conn
