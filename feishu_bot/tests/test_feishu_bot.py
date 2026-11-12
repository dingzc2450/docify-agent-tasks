"""feishu_bot 离线单测：指令解析、卡片结构、Gitea 客户端（注入假 requester）、编排层。"""
import json
import time

import pytest

import cards
import commands
from gitea_client import GiteaClient, GiteaError
from handler import handle_text


# ---------- 指令解析 ----------

class TestParseCommand:
    def test_plain_list(self):
        cmd = commands.parse_command("工单列表")
        assert cmd.name == "list" and cmd.label is None and cmd.page == 1

    def test_list_with_label(self):
        cmd = commands.parse_command("工单列表 bug")
        assert cmd.name == "list" and cmd.label == "bug" and cmd.page == 1

    def test_list_with_label_and_page(self):
        cmd = commands.parse_command("工单列表 bug 2")
        assert cmd.name == "list" and cmd.label == "bug" and cmd.page == 2

    def test_list_bare_page(self):
        cmd = commands.parse_command("工单列表 3")
        assert cmd.name == "list" and cmd.label is None and cmd.page == 3

    def test_group_mention_stripped(self):
        cmd = commands.parse_command("@_user_1 工单列表 frontend")
        assert cmd.name == "list" and cmd.label == "frontend"

    def test_create_with_body(self):
        cmd = commands.parse_command("新建工单 首页白屏 | 切日语后整页白屏")
        assert cmd.name == "create" and cmd.title == "首页白屏" and cmd.body == "切日语后整页白屏"

    def test_create_fullwidth_pipe(self):
        cmd = commands.parse_command("新建工单 标题 ｜ 描述")
        assert cmd.name == "create" and cmd.title == "标题" and cmd.body == "描述"

    def test_create_without_body_accepted(self):
        cmd = commands.parse_command("新建工单 只有标题")
        assert cmd.name == "create" and cmd.title == "只有标题" and cmd.body == ""

    def test_create_missing_title_falls_back_to_help(self):
        assert commands.parse_command("新建工单").name == "help"
        assert commands.parse_command("新建工单  | 只有描述").name == "help"

    def test_unrecognized_and_empty_fall_back_to_help(self):
        assert commands.parse_command("随便说点什么").name == "help"
        assert commands.parse_command("").name == "help"
        assert commands.parse_command("帮助").name == "help"


# ---------- 卡片结构 ----------

def _assert_card_shape(card):
    # 发送前最基本的结构保证；渲染效果仍以卡片搭建工具为准
    json.dumps(card, ensure_ascii=False)
    assert card["config"]["wide_screen_mode"] is True
    assert card["header"]["title"]["tag"] == "plain_text"
    assert isinstance(card["elements"], list) and card["elements"]


class TestCards:
    def test_usage_card(self):
        _assert_card_shape(cards.build_usage_card())

    def test_error_card(self):
        _assert_card_shape(cards.build_error_card("boom"))

    def test_list_card_with_issues(self):
        from gitea_client import GiteaIssue
        issues = [GiteaIssue(number=1, title="t", body="", labels=["bug"],
                             html_url="https://x/1", updated_at="2026-09-21")]
        card = cards.build_issue_list_card(issues, "docify/docify-agent", "bug", 1)
        _assert_card_shape(card)
        assert "第 1 页" in card["header"]["title"]["content"]

    def test_list_card_empty(self):
        card = cards.build_issue_list_card([], "docify/docify-agent", None, 5)
        _assert_card_shape(card)

    def test_created_card(self):
        from gitea_client import GiteaIssue
        issue = GiteaIssue(number=42, title="t", body="", labels=[],
                           html_url="https://x/42", updated_at="2026-09-21")
        _assert_card_shape(cards.build_created_card(issue, "docify/docify-agent"))


# ---------- Gitea 客户端 ----------

def _issue_payload(number, title, labels=()):
    return {
        "number": number, "title": title, "body": "",
        "labels": [{"name": l} for l in labels],
        "html_url": f"https://code.docify.jp/x/{number}",
        "updated_at": "2026-09-20T10:00:00Z",
    }


def _requester_ok(payload):
    def requester(method, url, params, json, headers):
        return 200, payload, {}
    return requester


class TestGiteaClient:
    def test_list_open_issues_params(self):
        seen = {}

        def requester(method, url, params, json, headers):
            seen.update(params)
            return 200, [_issue_payload(1, "a", ["bug"])], {}

        client = GiteaClient(token="t", requester=requester)
        issues = client.list_open_issues(label="bug", page=2)
        assert seen["state"] == "open" and seen["type"] == "issues"
        assert seen["labels"] == "bug" and seen["page"] == 2
        assert issues[0].labels == ["bug"] and issues[0].updated_at == "2026-09-20"

    def test_list_filters_label_client_side(self):
        # 服务端 labels 参数没过滤干净时，客户端兜底剔除
        payload = [_issue_payload(1, "a", ["bug"]), _issue_payload(2, "b", ["ui"])]
        client = GiteaClient(token="t", requester=_requester_ok(payload))
        issues = client.list_open_issues(label="bug")
        assert [i.number for i in issues] == [1]

    def test_create_requires_token(self):
        client = GiteaClient(token="", requester=_requester_ok({}))
        with pytest.raises(GiteaError, match="GITEA_TOKEN"):
            client.create_issue("t")

    def test_create_returns_issue(self):
        client = GiteaClient(token="t", requester=_requester_ok(_issue_payload(9, "new")))
        assert client.create_issue("new").number == 9

    def test_retry_on_429_then_success(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {"n": 0}

        def requester(method, url, params, json, headers):
            calls["n"] += 1
            if calls["n"] < 3:
                return 429, {"message": "rate limited"}, {"Retry-After": "0"}
            return 200, [_issue_payload(1, "a")], {}

        client = GiteaClient(token="t", requester=requester)
        assert len(client.list_open_issues()) == 1 and calls["n"] == 3

    def test_retry_exhausted_raises(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        client = GiteaClient(token="t",
                             requester=lambda *a: (500, {"message": "boom"}, {}))
        with pytest.raises(GiteaError, match="重试"):
            client.list_open_issues()

    def test_4xx_raises_without_retry(self):
        calls = {"n": 0}

        def requester(method, url, params, json, headers):
            calls["n"] += 1
            return 404, {"message": "Not Found"}, {}

        client = GiteaClient(token="t", requester=requester)
        with pytest.raises(GiteaError, match="404"):
            client.list_open_issues()
        assert calls["n"] == 1


# ---------- 编排层 ----------

class _FakeGitea:
    repo = "docify/docify-agent"
    page_size = 10

    def __init__(self, fail=False):
        self.fail = fail

    def list_open_issues(self, label=None, page=1):
        if self.fail:
            raise GiteaError("Gitea 请求多次重试仍失败")
        from gitea_client import GiteaIssue
        return [GiteaIssue(number=1, title="t", body="", labels=[],
                           html_url="https://x/1", updated_at="2026-09-21")]

    def create_issue(self, title, body=""):
        if self.fail:
            raise GiteaError("Gitea 请求失败（HTTP 403）：token 无效")
        from gitea_client import GiteaIssue
        return GiteaIssue(number=2, title=title, body=body, labels=[],
                          html_url="https://x/2", updated_at="2026-09-21")


class TestHandler:
    def test_help_card(self):
        card = handle_text("帮助", _FakeGitea())
        assert "用法" in card["header"]["title"]["content"]

    def test_list_card(self):
        card = handle_text("工单列表", _FakeGitea())
        assert "工单列表" in card["header"]["title"]["content"]

    def test_create_card(self):
        card = handle_text("新建工单 标题 | 描述", _FakeGitea())
        assert card["header"]["template"] == "green"

    def test_gitea_failure_degrades_to_error_card(self):
        # 优雅降级：Gitea 挂了也要回用户一张错误卡片，不沉默
        card = handle_text("工单列表", _FakeGitea(fail=True))
        assert card["header"]["template"] == "red"
        card2 = handle_text("新建工单 t", _FakeGitea(fail=True))
        assert card2["header"]["template"] == "red"
