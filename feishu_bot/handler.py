"""指令 → Gitea → 卡片 的编排层。

纯函数 handle_text(text, gitea) -> 卡片 dict，不 import 飞书 SDK，
本地 mock 与单测都走这一层。
"""
from __future__ import annotations

import cards
from commands import parse_command
from gitea_client import GiteaClient, GiteaError


def handle_text(text: str, gitea: GiteaClient) -> dict:
    """处理一条用户消息，返回要回复的消息卡片。任何异常都降级成错误卡片。"""
    command = parse_command(text)

    if command.name == "help":
        return cards.build_usage_card()

    try:
        if command.name == "list":
            issues = gitea.list_open_issues(label=command.label, page=command.page)
            return cards.build_issue_list_card(
                issues, repo=gitea.repo, label=command.label,
                page=command.page, page_size=gitea.page_size)

        if command.name == "create":
            issue = gitea.create_issue(command.title or "", command.body)
            return cards.build_created_card(issue, repo=gitea.repo)

        return cards.build_usage_card()
    except GiteaError as exc:
        return cards.build_error_card(f"Gitea 调用失败：{exc}")
    except Exception as exc:  # 兜底：任何未预期异常都不能让用户面对沉默
        return cards.build_error_card(f"处理指令时出现未预期错误：{exc!r}")
