"""Small shared utilities."""
from __future__ import annotations

import datetime


def utcnow() -> datetime.datetime:
    """Return the current time as a naive UTC datetime.

    SQLite does not preserve timezone info on DateTime columns, so we
    consistently store and compare naive UTC datetimes throughout the app.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
