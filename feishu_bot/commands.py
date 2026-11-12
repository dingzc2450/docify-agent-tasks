"""聊天指令解析。

支持的首版指令（群里 @机器人 或私聊直接发）：
  工单列表                → 列出 open 工单第 1 页
  工单列表 bug            → 按标签 bug 筛选
  工单列表 bug 2          → 第 2 页
  新建工单 标题 | 描述     → 创建 Gitea issue（描述可省略，竖线可用全角｜）
  帮助 / help             → 用法说明

群里收到的文本会带 @_user_1 这种 @占位符，解析前先剥掉。
识别不了的指令不沉默——返回 help，由上层渲染成用法卡片。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_MENTION_RE = re.compile(r"@_user_\d+")

HELP_TEXT = (
    "支持的指令：\n"
    "· 工单列表 [标签] [页码] —— 查询 open 工单\n"
    "· 新建工单 <标题> | <描述> —— 创建工单\n"
    "· 帮助 —— 查看本说明"
)


@dataclass
class Command:
    name: str  # "list" | "create" | "help"
    label: Optional[str] = None
    page: int = 1
    title: Optional[str] = None
    body: str = ""


def parse_command(raw_text: str) -> Command:
    text = _MENTION_RE.sub("", raw_text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return Command(name="help")

    if text.startswith("工单列表"):
        return _parse_list(text[len("工单列表"):].strip())

    if text.startswith("新建工单"):
        return _parse_create(text[len("新建工单"):].strip())

    # 「帮助」「help」以及其他一切无法识别的输入，统一回用法说明
    return Command(name="help")


def _parse_list(rest: str) -> Command:
    label: Optional[str] = None
    page = 1
    if rest:
        tokens = rest.split(" ")
        if tokens[0].isdigit():
            # 「工单列表 2」视为直接翻页，不带标签
            page = max(1, int(tokens[0]))
        else:
            label = tokens[0] or None
            if len(tokens) > 1 and tokens[1].isdigit():
                page = max(1, int(tokens[1]))
    return Command(name="list", label=label, page=page)


def _parse_create(rest: str) -> Command:
    # 描述可省略：「新建工单 标题」也接受，正文留空
    parts = re.split(r"[|｜]", rest, maxsplit=1)
    title = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""
    if not title:
        return Command(name="help")
    return Command(name="create", title=title, body=body)
