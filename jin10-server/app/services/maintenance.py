"""Periodic archive maintenance; business records and original assets are permanent."""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Material, utcnow


log = logging.getLogger(__name__)


def _referenced_assets(material: Material) -> set[str]:
    ids = {img.get("asset_id") for img in material.images or []}
    for module in (material.interpretation or {}).get("modules", []):
        ids |= {img.get("asset_id") for img in module.get("images", [])}
    ids |= set((material.edits or {}).get("images") or [])
    return {i for i in ids if i}


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def purge(db: Session, retention_days: int, now: dt.datetime | None = None) -> dict[str, Any]:
    """Business records are permanent archives; retention only bounds UI windows.

    Do not hard-delete source material, edits, boards, parse evidence or assets.
    Their original bytes and structured records are needed for later exports and
    reconstruction. Derived-file cleanup has its own reference-aware lifecycle.
    """
    return {'materials': 0, 'assets': 0, 'archive_preserved': True}


class MaintenanceWorker(threading.Thread):
    def __init__(self, session_factory: sessionmaker[Session], retention_days: int, interval_seconds: int = 6 * 3600):
        super().__init__(name="maintenance", daemon=True)
        self._factory = session_factory
        self._retention = retention_days
        self._interval = interval_seconds
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                with self._factory() as db:
                    purge(db, self._retention)
            except Exception:
                log.exception("maintenance failed")
            self._halt.wait(self._interval)

    def stop(self) -> None:
        self._halt.set()
