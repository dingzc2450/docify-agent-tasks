"""Gitea issue 查询 / 创建封装。

只依赖环境变量配置，不 import 飞书 SDK，可独立单测：
  GITEA_BASE_URL  默认 https://code.docify.jp
  GITEA_REPO      默认 docify/docify-agent
  GITEA_TOKEN     查询公开仓库可空，创建工单必填

所有请求带重试（429 读 Retry-After，5xx 指数退避），失败抛 GiteaError，
由上层转成友好的错误卡片（优雅降级，不让用户面对沉默或堆栈）。
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DEFAULT_BASE_URL = "https://code.docify.jp"
DEFAULT_REPO = "docify/docify-agent"

MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 0.8


class GiteaError(RuntimeError):
    """对用户可读的 Gitea 调用失败。"""


@dataclass
class GiteaIssue:
    number: int
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    html_url: str = ""
    updated_at: str = ""


# requester(method, path, params, json, headers) -> (status_code, payload, headers)
Requester = Callable[..., tuple[int, Any, dict]]


class GiteaClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        repo: Optional[str] = None,
        token: Optional[str] = None,
        requester: Optional[Requester] = None,
        page_size: int = 10,
    ):
        self.base_url = (base_url or os.environ.get("GITEA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.repo = repo or os.environ.get("GITEA_REPO") or DEFAULT_REPO
        self.token = token if token is not None else os.environ.get("GITEA_TOKEN", "")
        self.page_size = page_size
        self._requester = requester or _http_request

    # ---- 业务接口 ----

    def list_open_issues(self, label: Optional[str] = None, page: int = 1) -> list[GiteaIssue]:
        """拉取 open 工单（排除 PR），可按标签名筛选、按页翻页。"""
        params = {
            "state": "open",
            "type": "issues",  # 排除 PR，Gitea 的 issues 接口默认把 PR 也带出来
            "limit": self.page_size,
            "page": page,
        }
        if label:
            params["labels"] = label
        _, data, _ = self._request("GET", f"/api/v1/repos/{self.repo}/issues", params=params)
        issues = [self._to_issue(item) for item in data or []]
        if label:
            # 服务端 labels 参数按名字过滤的行为各版本有差异，客户端再过滤一层兜底
            issues = [i for i in issues if label in i.labels]
        return issues

    def create_issue(self, title: str, body: str = "") -> GiteaIssue:
        if not self.token:
            raise GiteaError("创建工单需要配置 GITEA_TOKEN（当前环境未配置）")
        _, data, _ = self._request(
            "POST", f"/api/v1/repos/{self.repo}/issues",
            json={"title": title, "body": body},
        )
        return self._to_issue(data)

    # ---- 内部 ----

    def _request(self, method: str, path: str, params: Optional[dict] = None,
                 json: Optional[dict] = None) -> tuple[int, Any, dict]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                status, payload, resp_headers = self._requester(
                    method, self.base_url + path, params, json, headers)
            except Exception as exc:  # 网络层错误
                last_error = exc
                self._sleep(attempt, None)
                continue
            if status == 429 or status >= 500:
                last_error = GiteaError(f"Gitea 返回 HTTP {status}")
                retry_after = _parse_retry_after(resp_headers.get("Retry-After"))
                self._sleep(attempt, retry_after)
                continue
            if status >= 400:
                msg = payload.get("message") if isinstance(payload, dict) else None
                raise GiteaError(f"Gitea 请求失败（HTTP {status}）：{msg or payload}")
            return status, payload, resp_headers
        raise GiteaError(f"Gitea 请求多次重试仍失败：{last_error}")

    @staticmethod
    def _sleep(attempt: int, retry_after: Optional[float]) -> None:
        if retry_after is not None:
            time.sleep(min(retry_after, 10))
        else:
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.2))

    @staticmethod
    def _to_issue(item: dict) -> GiteaIssue:
        labels = [l.get("name", "") for l in item.get("labels") or []]
        return GiteaIssue(
            number=item.get("number", 0),
            title=item.get("title", ""),
            body=item.get("body") or "",
            labels=[l for l in labels if l],
            html_url=item.get("html_url", ""),
            updated_at=(item.get("updated_at") or "")[:10],
        )


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _http_request(method: str, url: str, params: Optional[dict],
                  json: Optional[dict], headers: dict) -> tuple[int, Any, dict]:
    import requests  # 延迟导入：单测注入 requester 时不依赖 requests

    resp = requests.request(method, url, params=params, json=json,
                            headers=headers, timeout=15)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"message": resp.text[:200]}
    return resp.status_code, payload, dict(resp.headers)
