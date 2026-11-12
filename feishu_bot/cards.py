"""消息卡片构建（飞书互动卡片 v1 JSON）。

所有卡片都是纯 dict，发送方负责 json.dumps。构建时做最基本的结构保证，
上线前仍建议在「消息卡片搭建工具」里粘贴验证一遍渲染效果。
"""
from __future__ import annotations

from typing import Optional

from commands import HELP_TEXT
from gitea_client import GiteaIssue


def _card(template: str, title: str, elements: list[dict]) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": elements,
    }


def _md(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _note(content: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def build_usage_card() -> dict:
    return _card("grey", "工单助手 · 用法", [_md(HELP_TEXT)])


def build_error_card(message: str) -> dict:
    return _card("red", "操作失败", [
        _md(message),
        _note("如持续失败请联系管理员；机器人不会静默吞掉错误。"),
    ])


def build_issue_list_card(issues: list[GiteaIssue], repo: str,
                          label: Optional[str], page: int,
                          page_size: int = 10) -> dict:
    scope = f"标签 {label} · " if label else ""
    title = f"工单列表（{repo} · open · {scope}第 {page} 页）"
    if not issues:
        return _card("blue", title, [
            _md("没有符合条件的 open 工单。"),
            _note("发送「帮助」查看指令用法。"),
        ])

    elements: list[dict] = []
    for issue in issues:
        labels = " ".join(f"`{l}`" for l in issue.labels) or "无标签"
        elements.append(_md(
            f"**#{issue.number} [{issue.title}]({issue.html_url})**\n"
            f"{labels} · 更新于 {issue.updated_at or '未知'}"
        ))
    # 本页拉满说明大概率还有下一页，提示翻页指令（首版翻页走指令，不做按钮回调）
    if len(issues) >= page_size:
        next_cmd = f"工单列表 {label} {page + 1}" if label else f"工单列表 {page + 1}"
        elements.append(_note(f"可能还有下一页，发送「{next_cmd}」继续查看。"))
    return _card("blue", title, elements)


def build_created_card(issue: GiteaIssue, repo: str) -> dict:
    return _card("green", f"工单已创建（{repo}）", [
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**编号**\n#{issue.number}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**状态**\nopen"}},
            ],
        },
        _md(f"**标题**\n[{issue.title}]({issue.html_url})"),
        _note("Multica 侧会由工单同步任务自动带入，无需手动建单。"),
    ])
