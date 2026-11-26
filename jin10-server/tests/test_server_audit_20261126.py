"""Regression suite for the 2026-11-26 full-repository engineering audit."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select, text

from app.api import auth as auth_api, chat2api as chat2api_api
from app.briefs import (
    data_reliability,
    editorial_suite,
    gpu_editorial_ai,
    repair,
    sample_story,
    valuation_observations,
    volume_close,
    worker,
)
from app.briefs.editorial_authorization import adoption
from app.briefs.service import append_version, enqueue, ensure_issue
from app.briefs.source_archive import evidence_blob
from app.db.models import (
    BriefDataPoint,
    BriefJob,
    BriefSourceBlob,
    BriefSourceSnapshot,
    Feedback,
    ValuationObservation,
)
from app.ppt.pptx_xml import sanitize_xml_text
from app.ppt.textfit import wrap
from app.security import create_access_token, decode_token_claims
from app.services.data_export import BRIEF_BLOBS
from tests.conftest import login


CASES = json.loads(
    (Path(__file__).parent / "fixtures/editorial_suite_cases.json").read_text(encoding="utf-8")
)["cases"]


def _fixture_payload(category: str) -> dict:
    return deepcopy(
        next(row["payload"] for row in CASES if row["category"] == category and row["expect"]["status"] == "ok")
    )


def test_data_export_load_only_skips_binary_blob_decompression_and_handles_aware_bounds(db_factory, seed):
    team_id = seed["admin"].team_id
    blob_id = uuid.uuid4()
    sha = "a" * 64
    with db_factory() as db:
        # Insert raw non-gzip bytes directly into content so decompressing it would raise BadGzipFile/error.
        db.execute(
            text(
                "INSERT INTO brief_source_blobs (id, team_id, sha256, byte_count, content, created_at) "
                "VALUES (:id, :team_id, :sha256, :byte_count, :content, :created_at)"
            ),
            {
                "id": blob_id.hex,
                "team_id": team_id.hex,
                "sha256": sha,
                "byte_count": 24,
                "content": b"not-valid-gzip-payload!!",
                "created_at": datetime(2026, 11, 20, 8, 30, tzinfo=timezone.utc),
            },
        )
        db.commit()

    with db_factory() as db:
        columns = [col.key for col in BRIEF_BLOBS.columns]
        rows = list(BRIEF_BLOBS.rows(db, team_id, date(2026, 11, 1), date(2026, 11, 30)))
        assert "content" not in columns
        assert any(row["sha256"] == sha for row in rows)


def test_ppt_textfit_long_unbroken_token_is_linear_time_and_xml_sanitizes_control_chars():
    started = time.perf_counter()
    lines = wrap("X" * 25_000, width_in=3.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    assert len(lines) > 100 and "".join(lines) == "X" * 25_000

    cleaned = sanitize_xml_text("标题\x00\x07\x0b内容\n第二行\t制表\ufffe\uffff")
    assert cleaned == "标题内容\n第二行\t制表"


def test_auth_login_rate_limiting_and_single_pass_token_claims(client):
    auth_api._FAILED_ATTEMPTS.clear()
    try:
        for _ in range(auth_api._MAX_FAILED_ATTEMPTS):
            res = client.post(
                "/api/auth/login",
                json={"email": "editor@plus.test", "password": "wrong-password-123"},
            )
            assert res.status_code == 401
        blocked = client.post(
            "/api/auth/login",
            json={"email": "editor@plus.test", "password": "wrong-password-123"},
        )
        assert blocked.status_code == 429
    finally:
        auth_api._FAILED_ATTEMPTS.clear()

    uid, tid = uuid.uuid4(), uuid.uuid4()
    token, _ = create_access_token(uid, tid, auth_version=7)
    decoded_uid, decoded_ver = decode_token_claims(token)
    assert decoded_uid == uid and decoded_ver == 7


def test_chat2api_errors_endpoint_redacts_long_tokens_and_jwt_prefixes(client, monkeypatch):
    headers = login(client, "admin@plus.test")
    monkeypatch.setattr(
        chat2api_api,
        "_request_json",
        lambda method, path, **kw: {
            "error_tokens": [
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.supersecretpayload",
                "rt_1234567890abcdefghijklmnop",
                "bad",
            ]
        },
    )
    res = client.get("/api/chat2api/errors", headers=headers)
    assert res.status_code == 200
    body = res.json()
    tokens = body["error_tokens"]
    assert "supersecretpayload" not in json.dumps(body)
    assert "1234567890abcdefghijklmnop" not in json.dumps(body)
    assert tokens[2] == "bad"
    assert "..." in tokens[0] and "..." in tokens[1]


def test_feedback_utc_timestamps_keep_explicit_offset_on_sqlite(client, db_factory, seed):
    headers = login(client, "editor@plus.test")
    created = client.post(
        "/api/feedback",
        data={"title": "时区测试", "content": "测试时区序列化", "name": "编辑"},
        headers=headers,
    )
    assert created.status_code == 201
    fid = uuid.UUID(created.json()["id"])
    with db_factory() as db:
        row = db.get(Feedback, fid)
        row.completed_at = datetime(2026, 11, 26, 1, 15, tzinfo=timezone.utc)
        db.commit()
    listed = client.get("/api/feedback", headers=headers)
    assert listed.status_code == 200
    matched = next(item for item in listed.json()["items"] if item["id"] == str(fid))
    assert matched["completed_at"].endswith(("+00:00", "Z"))
    assert matched["created_at"].endswith(("+00:00", "Z"))


def test_quarantine_excludes_rejected_volume_and_intraday_dr_from_restored_series(db_factory, seed, tmp_path):
    team_id = seed["editor"].team_id
    with db_factory() as db:
        job = enqueue(db, team_id, date(2026, 11, 10), "volume", action="produce", user_id=seed["editor"].id)
        data_reliability.save_points(
            db,
            team_id,
            "volume",
            [
                {
                    "series": "volume",
                    "date": "2026-11-09",
                    "method": "jin10_hushen_turnover_yi_v1",
                    "values": {"date": "2026-11-09", "turn": "18500", "vol": "1200"},
                },
                {
                    "series": "volume",
                    "date": "2026-11-10",
                    "method": "jin10_hushen_turnover_yi_v1",
                    "quality_status": "rejected",
                    "values": {"date": "2026-11-10", "turn": "9100", "vol": "600", "reason": "before_close"},
                },
            ],
            job.id,
        )
        liq_job = enqueue(db, team_id, date(2026, 11, 10), "liquidity", action="produce", user_id=seed["editor"].id)
        data_reliability.save_points(
            db,
            team_id,
            "liquidity",
            [
                {
                    "series": "DR001",
                    "date": "2026-11-09",
                    "method": "cfets_dr_close_v1",
                    "values": {"date": "2026-11-09", "weighted_rate": "1.3100"},
                },
                {
                    "series": "DR007",
                    "date": "2026-11-09",
                    "method": "cfets_dr_close_v1",
                    "values": {"date": "2026-11-09", "weighted_rate": "1.3580"},
                },
                {
                    "series": "DR001",
                    "date": "2026-11-10",
                    "method": "cfets_dr_intraday_v1",
                    "values": {"date": "2026-11-10", "weighted_rate": "1.3159"},
                },
            ],
            liq_job.id,
        )
        db.commit()

        state_dir = tmp_path / "state"
        repair.restore_working_history(db, team_id, "volume", state_dir)
        vol_rows = json.loads((state_dir / "volume-history.json").read_text(encoding="utf-8"))["rows"]
        assert [r["date"] for r in vol_rows] == ["2026-11-09"]

        vol_series = data_reliability.read_series(
            db, team_id, "volume", "volume", date(2026, 11, 1), date(2026, 11, 30)
        )
        assert [r["data_date"] for r in vol_series["items"]] == ["2026-11-09"]

        repair.restore_working_history(db, team_id, "liquidity", state_dir)
        dr_rows = json.loads((state_dir / "dr-history.json").read_text(encoding="utf-8"))["rows"]
        assert [r["date"] for r in dr_rows] == ["2026-11-09"]


def test_volume_close_guard_blocks_preclose_confirmation_even_after_render_job(db_factory, seed, tmp_path):
    team_id = seed["editor"].team_id
    raw_preclose = json.dumps(
        {
            "data": [
                {
                    "date": "2026-11-10",
                    "sh_turn": "4000",
                    "sz_turn": "5100",
                    "sh_vol": "300",
                    "sz_vol": "300",
                    "updated_at": "2026-11-10T11:30:00+08:00",
                }
            ]
        }
    ).encode("utf-8")
    import hashlib

    digest = hashlib.sha256(raw_preclose).hexdigest()
    with db_factory() as db:
        first_job = enqueue(db, team_id, date(2026, 11, 10), "volume", action="produce", user_id=seed["editor"].id)
        first_job.status = "succeeded"
        blob = evidence_blob(db, team_id, digest, raw_preclose)
        db.add(
            BriefSourceSnapshot(
                team_id=team_id,
                job_id=first_job.id,
                attempt=1,
                category="volume",
                data_date=date(2026, 11, 10),
                relative_path="volume-raw-page-1.json",
                sha256=digest,
                byte_count=len(raw_preclose),
                blob=blob,
            )
        )
        render_job = enqueue(
            db, team_id, date(2026, 11, 10), "volume", action="render", user_id=seed["editor"].id, unique=True
        )
        render_folder = tmp_path / "runs" / str(render_job.id)
        render_folder.mkdir(parents=True, exist_ok=True)
        (render_folder / "plot-data.json").write_text(
            json.dumps([{"date": "2026-11-10", "turn": "9100", "vol": "600"}]),
            encoding="utf-8",
        )
        payload = {
            "title": "【A股温度计】测试",
            "dynamic_body": "2026-11-10，成交额。",
            "data_date": "2026-11-10",
            "facts": {"amount_yi": "9100", "previous_date": "2026-11-09"},
            "images": [],
            "valid": True,
            "artifact_dir": f"runs/{render_job.id}",
            "source_archive": {"job_id": str(first_job.id)},
        }
        db.commit()
        block = volume_close.draft_block(payload, render_folder, db=db, team_id=team_id)
        assert block is not None and "2026-11-10" in block[0]


def test_worker_poison_pill_protection_and_confirmed_version_preservation(db_factory, seed, monkeypatch):
    team_id = seed["editor"].team_id
    with db_factory() as db:
        poison = enqueue(db, team_id, date(2026, 11, 10), "volume", action="produce", user_id=seed["editor"].id)
        poison.status = "running"
        poison_id = poison.id
        db.commit()

    def crash_execute(_factory, _job_id):
        raise RuntimeError("simulated unexpected engine crash")

    monkeypatch.setattr(worker, "execute", crash_execute)
    worker.execute_safely(db_factory, poison_id)

    with db_factory() as db:
        failed = db.get(BriefJob, poison_id)
        assert failed.status == "failed"
        assert "simulated unexpected engine crash" in (failed.error or "")

        # Verify confirmed_version is preserved and preferred for latest-* follow-ups
        issue = ensure_issue(db, team_id, date(2026, 11, 11), "oil_etf")
        v1 = append_version(
            db,
            issue,
            {"title": "已确认v1", "dynamic_body": "正文v1", "data_date": "2026-11-10", "images": [], "valid": True},
        )
        issue.status = "confirmed"
        issue.confirmed_version = v1.number
        v2 = append_version(
            db,
            issue,
            {"title": "未确认草稿v2", "dynamic_body": "正文v2", "data_date": "2026-11-11", "images": [], "valid": True},
        )
        assert issue.confirmed_version == 1 and issue.version == 2
        db.commit()


def test_gpu_editorial_ai_parses_escaped_and_fenced_json_and_sample_story_handles_edge_cases(monkeypatch):
    monkeypatch.setattr(gpu_editorial_ai, "get_settings", lambda: SimpleNamespace(briefs_skill_ai_enabled=True))
    monkeypatch.setattr(
        gpu_editorial_ai,
        "briefs_ai",
        lambda _team: {
            "provider": "chat2api",
            "model": "gpt-4o",
            "base_url": "http://chat2api.local/v1",
            "key": "sk-test",
        },
    )
    options = {
        "headline": [{"id": "h1"}],
        "lead": [{"id": "l1"}],
        "comparison": [{"id": "c1"}],
        "closing": [{"id": "x1"}],
    }
    expected_plan = {"headline": "h1", "lead": "l1", "comparison": "c1", "closing": "x1"}

    class _Reply:
        def __init__(self, wrapped: str):
            self._raw = json.dumps(
                {"model": "gpt-4o", "choices": [{"finish_reason": "stop", "message": {"content": wrapped}}]}
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit=-1):
            return self._raw[:limit] if limit >= 0 else self._raw

    for wrapped in (
        f"<escape>{json.dumps(expected_plan)}</escape>",
        f"```json\n{json.dumps(expected_plan)}\n```",
    ):
        monkeypatch.setattr(gpu_editorial_ai, "urlopen", lambda req, timeout, w=wrapped: _Reply(w))
        res = gpu_editorial_ai.select_plan({}, {"options": options})
        assert res["status"] == "ok" and res["attempts"] == 1 and res["plan"] == expected_plan

    # Empty weekly_change_pct="" in token_cost must not crash sample_story with Decimal("") InvalidOperation
    monkeypatch.setattr(gpu_editorial_ai, "select_plan", lambda *_a, **_k: {"status": "not_configured"})
    token_payload = _fixture_payload("token_cost")
    token_payload["facts"].update(weekly_change_pct="", previous_date="", previous_price="")
    token_payload["dynamic_body"] = "截至2026-10-30，Token支出价格指数报0.9750美元/百万Token，七日变化基准缺失。"
    team_settings = {"brief_editorial": adoption("test-operator")}
    assert sample_story.apply_sample_story(token_payload, "token_cost", team_settings) is True
    assert token_payload["editorial"]["status"] == "active"

    # Mixed-date oil_etf must keep distinct fund dates without double leading dates or "同期，"
    oil_payload = _fixture_payload("oil_etf")
    oil_payload["data_date"] = "2026-10-30"
    oil_payload["facts"]["funds"][0].update(
        data_date="2026-10-29",
        previous_date="2026-10-28",
        change_barrels="250000",
        change_basis="futures_contracts_1000",
    )
    oil_payload["facts"]["funds"][1].update(
        data_date="2026-10-30",
        previous_date="2026-10-29",
        change_barrels="-120000",
        change_basis="futures_contracts_1000",
    )
    oil_payload["dynamic_body"] = (
        "2026-10-29，WTI（USO）名义敞口估算约1883.40万桶，其中期货持仓1583.60万桶，"
        "期货持仓较2026-10-28增加25.00万桶；"
        "2026-10-30，Brent（BNO）期货持仓约696.30万桶，期货持仓较2026-10-29减少12.00万桶。"
    )
    cand = editorial_suite.build_candidate("oil_etf", deepcopy(oil_payload))
    assert "2026-10-29，2026-10-29" not in cand["body"]
    assert "2026-10-30，2026-10-29" not in cand["body"]
    assert "同期，" not in cand["body"]
    assert "2026-10-29，WTI（USO）" in cand["body"] and "2026-10-30，Brent（BNO）" in cand["body"]

    assert sample_story.apply_sample_story(oil_payload, "oil_etf", team_settings) is True
    assert oil_payload["editorial"]["status"] == "active"
    assert "2026-10-29，2026-10-29" not in oil_payload["dynamic_body"]
    assert "2026-10-30，2026-10-29" not in oil_payload["dynamic_body"]
    assert "2026-10-29，WTI" in oil_payload["dynamic_body"] and "2026-10-30，Brent" in oil_payload["dynamic_body"]


def test_valuation_observations_preserves_original_job_id_on_unchanged_rows(db_factory, seed, tmp_path):
    team_id = seed["editor"].team_id
    snap = tmp_path / "erp-snapshot.json"
    rows = [
        {"date": "2026-10-29", "close": "3900.00", "pe_ttm_mcw": "12.50", "bond_yield_number": "2.1000"},
        {"date": "2026-10-30", "close": "3920.00", "pe_ttm_mcw": "12.60", "bond_yield_number": "2.1200"},
    ]
    snap.write_text(json.dumps(rows), encoding="utf-8")
    with db_factory() as db:
        job1 = enqueue(db, team_id, date(2026, 10, 30), "valuation", action="produce", user_id=seed["editor"].id)
        job1.status = "succeeded"
        job2 = enqueue(
            db, team_id, date(2026, 10, 30), "valuation", action="refresh", user_id=seed["editor"].id, unique=True
        )
        db.commit()

        assert valuation_observations.save_history(db, team_id, job1.id, snap) == 2
        db.commit()

        # Modify only 2026-10-30; 2026-10-29 must retain job1.id
        rows[1]["bond_yield_number"] = "2.1150"
        snap.write_text(json.dumps(rows), encoding="utf-8")
        assert valuation_observations.save_history(db, team_id, job2.id, snap) == 2
        db.commit()

        obs = {
            r.date.isoformat(): r
            for r in db.scalars(select(ValuationObservation).where(ValuationObservation.team_id == team_id))
        }
        assert obs["2026-10-29"].source_job_id == job1.id
        assert obs["2026-10-30"].source_job_id == job2.id
        assert obs["2026-10-30"].bond_yield_number == Decimal("2.1150")
