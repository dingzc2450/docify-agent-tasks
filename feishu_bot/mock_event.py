#!/usr/bin/env python3
"""本地 mock 自测：不依赖飞书凭证、不连 Gitea，走完整指令链路并打印卡片。

用法：python3 mock_event.py
会用假数据跑一遍所有指令形态，输出渲染的卡片 JSON，可粘贴到飞书
「消息卡片搭建工具」里验证真实渲染效果。
"""
from __future__ import annotations

import json

from gitea_client import GiteaIssue
from handler import handle_text

SAMPLE_ISSUES = [
    GiteaIssue(number=128, title="工作台页面在移动端布局错乱",
               body="", labels=["bug", "frontend"],
               html_url="https://code.docify.jp/docify/docify-agent/issues/128",
               updated_at="2026-09-20"),
    GiteaIssue(number=125, title="文档导出 PDF 偶发失败",
               body="", labels=["bug", "backend"],
               html_url="https://code.docify.jp/docify/docify-agent/issues/125",
               updated_at="2026-09-18"),
]


class FakeGitea:
    repo = "docify/docify-agent"
    page_size = 10

    def list_open_issues(self, label=None, page=1):
        if label == "frontend":
            return [SAMPLE_ISSUES[0]]
        if page > 1:
            return []
        return SAMPLE_ISSUES

    def create_issue(self, title, body=""):
        return GiteaIssue(number=131, title=title, body=body, labels=[],
                          html_url="https://code.docify.jp/docify/docify-agent/issues/131",
                          updated_at="2026-09-21")


CASES = [
    "工单列表",
    "@_user_1 工单列表 frontend",
    "工单列表 2",
    "新建工单 首页加载白屏 | 切到日语后整页白屏，控制台报 500",
    "新建工单",      # 缺标题 → 用法卡片
    "随便说点什么",   # 无法识别 → 用法卡片
    "帮助",
]


def main() -> None:
    gitea = FakeGitea()
    for text in CASES:
        card = handle_text(text, gitea)
        # 确保卡片可被 JSON 序列化（发送时就是 json.dumps 出去的）
        payload = json.dumps(card, ensure_ascii=False, indent=2)
        print(f"===== 输入：{text!r} =====")
        print(payload)
        print()


if __name__ == "__main__":
    main()
