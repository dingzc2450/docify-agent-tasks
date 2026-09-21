#!/usr/bin/env python3
"""飞书应用机器人服务：事件订阅 → 指令处理 → 卡片回复。

凭证相关部分全部集中在本文件；commands / cards / handler / gitea_client
不依赖飞书 SDK，可离线自测（见 mock_event.py 与 tests/）。

环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET   应用凭证（必填）
  FEISHU_VERIFICATION_TOKEN           事件订阅 verification token（必填）
  FEISHU_ENCRYPT_KEY                  事件订阅 Encrypt Key（可选，建议配置）
  GITEA_TOKEN                         创建工单用（必填；只查询可空）
  GITEA_BASE_URL / GITEA_REPO         可选，默认 code.docify.jp / docify/docify-agent
  PORT                                可选，默认 3000

运行：python3 server.py
"""
from __future__ import annotations

import json
import os
import threading
import time

import lark_oapi as lark
from lark_oapi.adapter.flask import parse_req, parse_resp
from flask import Flask, request

from gitea_client import GiteaClient
from handler import handle_text

app = Flask(__name__)
gitea = GiteaClient()

# ---- 幂等：飞书 3 秒未收到 200 会重推同一事件，按 message_id 去重 ----
_processed: dict[str, float] = {}
_processed_lock = threading.Lock()
DEDUP_TTL_SECONDS = 300


def _already_processed(message_id: str) -> bool:
    now = time.time()
    with _processed_lock:
        # 顺手清掉过期条目，防止长跑内存膨胀
        for key in [k for k, ts in _processed.items() if now - ts > DEDUP_TTL_SECONDS]:
            del _processed[key]
        if message_id in _processed:
            return True
        _processed[message_id] = now
        return False


def _do_reply(client: lark.Client, message_id: str, card: dict) -> None:
    req = (
        lark.api.im.v1.ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.api.im.v1.ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        # 回复失败只记日志——用户侧已有卡片降级链路，这里再抛也无人可收
        app.logger.error("回复消息失败 code=%s msg=%s", resp.code, resp.msg)


def _on_message(data: lark.api.im.v1.P2ImMessageReceiveV1) -> None:
    event = data.event
    message = event.message
    if message.message_type != "text":
        return
    # 群聊里只响应 @机器人 的消息，私聊全部响应
    if message.chat_type == "group" and not message.mentions:
        return
    if _already_processed(message.message_id):
        return

    def work() -> None:
        try:
            text = json.loads(message.content).get("text", "")
        except (ValueError, AttributeError):
            text = ""
        card = handle_text(text, gitea)
        _do_reply(_client, message.message_id, card)

    # 立即返回 200，重活异步做，避免飞书超时重推
    threading.Thread(target=work, daemon=True).start()


_client = lark.Client.builder() \
    .app_id(os.environ["FEISHU_APP_ID"]) \
    .app_secret(os.environ["FEISHU_APP_SECRET"]) \
    .log_level(lark.LogLevel.INFO) \
    .build()  # tenant_access_token 由 SDK 内置缓存管理

_dispatcher = (
    lark.EventDispatcherHandler.builder(
        os.environ.get("FEISHU_ENCRYPT_KEY", ""),
        os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
    )
    .register_p2_im_message_receive_v1(_on_message)
    .build()
)


@app.route("/webhook/event", methods=["POST"])
def webhook_event():
    resp = _dispatcher.do(parse_req())
    return parse_resp(resp)


@app.route("/healthz", methods=["GET"])
def healthz():
    return {"ok": True}


def main() -> None:
    port = int(os.environ.get("PORT", "3000"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
