"""定期清理：素材只保留最近 N 天（含回收站），连同不再被引用的图片。

生成过的日报文件（PPTX / PDF）不受影响。
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Asset, Board, Material, ParseReport, utcnow

from .storage import asset_path

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
    cutoff = (now or utcnow()) - dt.timedelta(days=retention_days)
    stamp = func.coalesce(Material.publish_time, Material.collect_time)
    old = db.scalars(select(Material).where(stamp < cutoff)).all()
    for m in old:
        db.delete(m)
    db.flush()

    keep: dict[Any, set[str]] = {}
    for m in db.scalars(select(Material)):
        keep.setdefault(m.team_id, set()).update(_referenced_assets(m))
    removed_assets = 0
    for asset in db.scalars(select(Asset)).all():
        # 刚上传、素材还没推送上来的图片不清理
        if asset.id in keep.get(asset.team_id, set()) or _aware(asset.created_at) >= cutoff:
            continue
        asset_path(asset.team_id, asset.id).unlink(missing_ok=True)
        db.delete(asset)
        removed_assets += 1

    db.execute(delete(Board).where(Board.day < cutoff.date()))
    db.execute(delete(ParseReport).where(ParseReport.created_at < cutoff))
    db.commit()
    result = {"materials": len(old), "assets": removed_assets}
    if old or removed_assets:
        log.info("purged %s", result)
    return result


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
