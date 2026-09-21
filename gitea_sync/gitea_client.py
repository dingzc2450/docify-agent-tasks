#!/usr/bin/env python3
"""Gitea REST API 封装（docify 实例 https://code.docify.jp）。

只做本仓库两个工具需要的面：列工单 / 列标签 / 建工单 / 建标签。
认证走环境变量 GITEA_TOKEN（header: Authorization: token ***），
token 不入库、不入日志——报错信息里也只带 HTTP 状态码和 Gitea 返回的 message。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://code.docify.jp/api/v1"
OWNER = "docify"
REPO = "docify-agent"


class GiteaError(RuntimeError):
    """Gitea API 调用失败（含重试耗尽）。"""


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def get_token() -> str:
    tok = os.environ.get("GITEA_TOKEN", "").strip()
    if not tok:
        raise GiteaError("缺少环境变量 GITEA_TOKEN，请先配置（token 只走环境变量，不写进代码）。")
    return tok


def request(method: str, path: str, body: dict | None = None,
            max_retries: int = 4) -> tuple[dict | list, dict]:
    """发请求，返回 (json_payload, response_headers)。

    429 读 Retry-After、5xx 指数退避；4xx（除 429）直接抛错不重试。
    """
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json",
               "Authorization": f"token {get_token()}"}
    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                return (json.loads(raw) if raw.strip() else {}), dict(resp.headers)
        except urllib.error.HTTPError as e:
            last = e
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            if e.code == 429 and attempt < max_retries - 1:
                wait = int(e.headers.get("Retry-After") or 2 ** attempt)
                log(f"HTTP 429 限流，{wait}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            if e.code in (500, 502, 503, 504) and attempt < max_retries - 1:
                w = 2 ** attempt
                log(f"HTTP {e.code}，{w}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(w)
                continue
            raise GiteaError(f"Gitea API {method} {path} → HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < max_retries - 1:
                w = 2 ** attempt
                log(f"网络异常 {e}，{w}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(w)
                continue
            raise GiteaError(f"Gitea API {method} {path} 网络失败: {e}") from e
    raise GiteaError(f"Gitea API {method} {path} 已重试 {max_retries} 次仍失败: {last}")


def list_open_issues(state: str = "open", limit: int = 50) -> list[dict]:
    """拉工单（type=issues 排除 PR），自动翻页拉全。

    Gitea 的 /issues 在 type 缺省时会把 PR 混进来，必须显式 type=issues。
    """
    issues: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode({"state": state, "type": "issues",
                                    "limit": limit, "page": page})
        batch, _ = request("GET", f"/repos/{OWNER}/{REPO}/issues?{q}")
        if not batch:
            break
        issues += batch
        if len(batch) < limit:
            break
        page += 1
        if page > 100:   # 防跑飞
            raise GiteaError("工单翻页超过 100 页，疑似分页失效，停止")
    return issues


def list_labels() -> list[dict]:
    labels: list[dict] = []
    page = 1
    while True:
        batch, _ = request("GET",
                           f"/repos/{OWNER}/{REPO}/labels?limit=50&page={page}")
        if not batch:
            break
        labels += batch
        if len(batch) < 50:
            break
        page += 1
    return labels


def create_issue(title: str, body: str, label_ids: list[int] | None = None) -> dict:
    payload: dict = {"title": title, "body": body}
    if label_ids:
        payload["labels"] = label_ids
    issue, _ = request("POST", f"/repos/{OWNER}/{REPO}/issues", body=payload)
    return issue


def create_label(name: str, color: str = "#1f6feb") -> dict:
    label, _ = request("POST", f"/repos/{OWNER}/{REPO}/labels",
                       body={"name": name, "color": color})
    return label


def issue_html_url(number: int) -> str:
    return f"https://code.docify.jp/{OWNER}/{REPO}/issues/{number}"
