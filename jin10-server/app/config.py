"""服务配置，全部来自环境变量（前缀 PLUS_），见 .env.example"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://plus:plus@localhost:5432/plus"
    #: JWT 签名密钥，生产环境必须设置为随机长字符串
    jwt_secret: str = Field(default="dev-only-change-me", min_length=16)
    jwt_expire_hours: int = 24 * 90
    #: 素材（含回收站与图片）保留天数，超过的自动清理
    retention_days: int = 7
    #: 素材图片、生成的 PPT 等文件存放目录
    storage_dir: Path = Path("data/storage")
    #: 单张图片上限
    max_asset_bytes: int = 15 * 1024 * 1024
    #: 反馈示意图上限
    max_feedback_bytes: int = 5 * 1024 * 1024
    #: 单次同步最多条数
    max_sync_batch: int = 50

    # —— AI 分析（TypeSafe JEV：只做栏目分类与重要度评分，不改写任何文字）——
    jev_api_key: str = ""
    jev_model: str = "jev-latest"
    jev_base_url: str = "https://api.typesafe.ai"
    #: 后台分析线程；未配置 JEV Key 时自动关闭
    ai_worker_enabled: bool = True
    ai_worker_interval_seconds: float = 5.0
    ai_worker_batch: int = 10
    #: 单条素材最多尝试次数，超过后标记为 failed
    ai_max_attempts: int = 3

    # Fixed briefs use a separate optional provider and a separate worker.
    briefs_deepseek_api_key: str = ''
    briefs_deepseek_model: str = ''


@lru_cache
def get_settings() -> Settings:
    return Settings()
