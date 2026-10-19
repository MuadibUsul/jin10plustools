"""TypeSafe System One（JEV）接口客户端。文档：https://docs.typesafe.ai

POST {base}/v1/systemone  {"model", "state", "questions"} → {"model", "answers", "usage"}
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import get_settings

_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class JevError(Exception):
    """JEV 调用失败。retryable 表示稍后重试可能成功（限流、超时、服务端错误）"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class JevClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "jev-latest",
        base_url: str = "https://api.typesafe.ai",
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("未配置 PLUS_JEV_API_KEY")
        self.model = model
        self.max_retries = max_retries
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    @classmethod
    def from_settings(cls) -> JevClient:
        s = get_settings()
        return cls(s.jev_api_key, model=s.jev_model, base_url=s.jev_base_url)

    def close(self) -> None:
        self._http.close()

    def evaluate(self, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """一次请求评估全部问题，返回 answers（按问题名索引）"""
        body = {"model": self.model, "state": state, "questions": questions}
        for attempt in range(self.max_retries + 1):
            try:
                res = self._http.post("/v1/systemone", json=body)
            except httpx.TransportError as err:
                error = JevError(f"无法连接 JEV：{err}", retryable=True)
            else:
                if res.status_code == 200:
                    answers = res.json().get("answers")
                    if not isinstance(answers, dict):
                        raise JevError("JEV 返回格式异常：缺少 answers", retryable=False)
                    return answers
                error = JevError(
                    f"JEV 返回 HTTP {res.status_code}",
                    retryable=res.status_code in _RETRY_STATUS,
                )
            if not error.retryable or attempt == self.max_retries:
                raise error
            time.sleep(min(2**attempt, 8))
        raise AssertionError("unreachable")
