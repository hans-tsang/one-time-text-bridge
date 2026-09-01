"""Cleanup of expired/consumed messages.

Run on app startup, on a recurring interval within the app process, and
available as a standalone CLI: ``python -m app.cleanup``.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.database import SessionLocal, init_db
from app.services.messages import cleanup_expired_and_consumed

logger = logging.getLogger("app.cleanup")


def run_cleanup_once() -> int:
    db = SessionLocal()
    try:
        deleted = cleanup_expired_and_consumed(db)
        logger.info("cleanup removed %d expired/consumed message(s)", deleted)
        return deleted
    finally:
        db.close()


async def run_cleanup_forever(interval_seconds: int | None = None) -> None:
    interval = interval_seconds or settings.cleanup_interval_seconds
    while True:
        try:
            run_cleanup_once()
        except Exception:  # noqa: BLE001 - never let cleanup crash the loop
            logger.exception("cleanup task failed")
        await asyncio.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()
    run_cleanup_once()


if __name__ == "__main__":
    main()
