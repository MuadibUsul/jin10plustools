"""后台分析：轮询待分析素材 → 调用 JEV → 写回栏目与评分。

- 「加入并 AI 分析」的素材优先，其余按采集时间从新到旧
- PostgreSQL 下用 FOR UPDATE SKIP LOCKED 认领，多个实例也不会重复分析
- 限流、超时等可重试错误稍后重试，超过 ai_max_attempts 次标记为 failed
- 写回时更新 updated_at，插件下次增量同步即可拿到分析结果
"""

from __future__ import annotations

import logging
import threading
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Material, Team, utcnow
from app.services.team_settings import provider

from .analysis import QUESTIONS, ScoringConfig, apply_answers, build_state
from .jev_client import JevClient, JevError

log = logging.getLogger("plus.ai")


def reset_interrupted(db: Session) -> None:
    """进程重启时，上次中断的「分析中」重新排队"""
    db.execute(update(Material).where(Material.ai_status == "analyzing").values(ai_status="pending"))
    db.commit()


def claim_batch(db: Session, limit: int, team_ids=None) -> list[uuid.UUID]:
    query = (
        select(Material)
        .where(Material.ai_status == "pending", Material.deleted_at.is_(None))
        .order_by(Material.analyze_requested.desc(), Material.collect_time.desc())
        .limit(limit)
    )
    if team_ids is not None:
        query = query.where(Material.team_id.in_(team_ids))
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = list(db.scalars(query))
    for m in rows:
        m.ai_status = "analyzing"
    db.commit()
    return [m.id for m in rows]


def analyze_one(factory: sessionmaker[Session], client: JevClient | None, material_id: uuid.UUID, max_attempts: int) -> str:
    """分析单条素材，返回最终 ai_status"""
    with factory() as db:
        m = db.get(Material, material_id)
        if m is None or m.ai_status != "analyzing":
            return "skipped"
        state = build_state(m)
        team = db.get(Team, m.team_id)
        config = provider(team, 'jev')
        override = bool((team.settings or {}).get('ai', {}).get('jev'))

    owned_client = None
    if override or client is None:
        if not config['key']:
            with factory() as db:
                m = db.get(Material, material_id)
                if m and m.ai_status == 'analyzing':
                    m.ai_status = 'pending'
                    db.commit()
            return 'pending'
        owned_client = JevClient(config['key'], model=config['model'], base_url=get_settings().jev_base_url)
        client = owned_client

    try:
        answers = client.evaluate(state, QUESTIONS)
        error: JevError | None = None
    except JevError as err:
        answers, error = None, err
    finally:
        if owned_client:
            owned_client.close()

    with factory() as db:
        m = db.get(Material, material_id)
        if m is None:
            return "skipped"
        now = utcnow()
        m.ai_attempts += 1
        if answers is not None:
            team = db.get(Team, m.team_id)
            apply_answers(m, answers, ScoringConfig.from_team_settings(team.settings if team else None), now)
            m.ai_status, m.ai_error, m.analyzed_at = "done", None, now
        else:
            assert error is not None
            retry = error.retryable and m.ai_attempts < max_attempts
            m.ai_status = "pending" if retry else "failed"
            m.ai_error = str(error)
            log.warning("analyze %s failed (attempt %s): %s", material_id, m.ai_attempts, error)
        m.updated_at = now
        db.commit()
        return m.ai_status


def run_once(factory: sessionmaker[Session], client: JevClient | None) -> int:
    settings = get_settings()
    with factory() as db:
        eligible = [team.id for team in db.scalars(select(Team))
                    if provider(team, 'jev')['key'] or (client is not None and not (team.settings or {}).get('ai', {}).get('jev'))]
        ids = claim_batch(db, settings.ai_worker_batch, eligible)
    for mid in ids:
        analyze_one(factory, client, mid, settings.ai_max_attempts)
    return len(ids)


class AnalysisWorker(threading.Thread):
    def __init__(self, factory: sessionmaker[Session], client: JevClient | None, interval: float) -> None:
        super().__init__(name="plus-ai-worker", daemon=True)
        self._factory = factory
        self._client = client
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        with self._factory() as db:
            reset_interrupted(db)
        log.info("AI worker started (team configuration enabled)")
        while not self._stop_event.is_set():
            try:
                processed = run_once(self._factory, self._client)
            except Exception:  # 单轮失败不影响后续轮询
                log.exception("AI worker iteration failed")
                processed = 0
            if processed == 0:
                self._stop_event.wait(self._interval)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=10)
        if self._client:
            self._client.close()
