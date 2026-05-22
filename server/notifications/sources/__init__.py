"""Registered source materializers.

Each module exposes a `materialize(user, db) -> int` function and a
SOURCE constant matching one of server.models.NOTIFICATION_SOURCES.
"""

from . import acclaim, fallen, fold_community, fold_inbox, fold_thread, midden

ALL = (fold_community, fold_thread, fold_inbox, fallen, midden, acclaim)
