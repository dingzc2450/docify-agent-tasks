# 飞书工单机器人（MYD-302 第 2 条线：聊天会话查/建工单）

在飞书里通过聊天指令查询、创建 Gitea 工单（默认 `code.docify.jp/docify/docify-agent`）。
**只写 Gitea**——Multica 侧由工单同步任务（MYD-302 第 1 条线）自动带入，聊天侧不直接建 Multica issue，避免双写不一致。

## 指令

私聊机器人，或群里 @机器人：

| 指令 | 说明 |
| --- | --- |
| `工单列表` | open 工单第 1 页（每页 10 条，排除 PR） |
| `工单列表 bug` | 按标签 `bug` 筛选 |
| `工单列表 bug 2` / `工单列表 2` | 翻页 |
| `新建工单 <标题> \| <描述>` | 创建 Gitea issue（描述可省，竖线支持全角｜） |
| `帮助` | 用法说明 |

识别不了的指令回用法卡片；Gitea 挂了回错误卡片——任何情况下机器人都不会沉默。

## 结构

| 文件 | 职责 | 依赖飞书凭证？ |
| --- | --- | --- |
| `commands.py` | 指令解析（剥 @_user_N 占位符） | 否 |
| `cards.py` | 消息卡片 JSON 构建 | 否 |
| `gitea_client.py` | Gitea 查询/建单封装（重试 + 429 处理） | 否 |
| `handler.py` | 指令 → Gitea → 卡片编排，异常降级 | 否 |
| `server.py` | 事件订阅接入（lark-oapi + Flask）、幂等去重、异步处理 | **是** |
| `mock_event.py` | 本地假数据跑全链路、打印卡片 | 否 |
| `tests/` | 离线单测（注入假 requester，不联网） | 否 |

`server.py` 之外的部分全部可离线开发自测——凭证没到位前先验证逻辑，凭证到位后直接联调。

## 本地自测（无需任何凭证）

```bash
cd feishu_bot
python3 mock_event.py                 # 打印各指令渲染的卡片 JSON
python3 -m pytest tests -q            # 离线单测
```

mock 输出的卡片 JSON 可粘贴到飞书[消息卡片搭建工具](https://open.feishu.cn/cardkit)验证真实渲染。

## 环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 是 | 应用凭证，运行时注入，不入库 |
| `FEISHU_VERIFICATION_TOKEN` | 是 | 事件订阅 verification token |
| `FEISHU_ENCRYPT_KEY` | 建议 | 事件订阅 Encrypt Key |
| `GITEA_TOKEN` | 建单必填 | 查询公开仓库可空 |
| `GITEA_BASE_URL` / `GITEA_REPO` | 否 | 默认 `https://code.docify.jp` / `docify/docify-agent` |
| `PORT` | 否 | 默认 3000 |

## 启动服务（凭证到位后）

```bash
pip install -r requirements.txt
export FEISHU_APP_ID=... FEISHU_APP_SECRET=... FEISHU_VERIFICATION_TOKEN=... GITEA_TOKEN=...
python3 server.py    # 监听 :3000，事件回调路径 /webhook/event
```

## 飞书开放平台配置步骤（管理员操作，约 15 分钟）

> 若复用 `feishu_dispatch` 在用的应用（环境变量同名 `FEISHU_APP_ID/SECRET`），从第 3 步开始；新应用从头走。

1. **创建企业自建应用**：[开放平台后台](https://open.feishu.cn/app) → 创建企业自建应用，拿到 `App ID` / `App Secret`（凭证管理页）。
2. **版本与可用范围**：创建测试版本，可用范围先圈定开发同学，避免未联调完成就全员可见。
3. **添加机器人能力**：应用能力 → 添加「机器人」。
4. **申请权限**（最小集）：权限管理 → 开通
   - `im:message`（获取与发送消息）
   - `im:message:send_as_bot`（以应用身份发消息）
5. **配置事件订阅**：事件与回调 → 事件订阅
   - 请求地址填 `https://<公网域名>/webhook/event`（开发期用内网穿透，如 `ngrok http 3000`）；
   - 保存时飞书会推 `url_verification` 挑战，SDK 自动应答（前提：服务已启动且 `FEISHU_VERIFICATION_TOKEN` 与后台一致）；
   - 添加事件 **`im.message.receive_v1`（接收消息）**；
   - 建议同时启用 Encrypt Key 加密，填入 `FEISHU_ENCRYPT_KEY`。
6. **发布版本**：权限和事件改动都要发新版本并等管理员审核通过才生效。
7. **把机器人拉进目标群**（或私聊直接搜机器人名）。

## 部署

- 开发：本机 `python3 server.py` + 内网穿透。
- 生产：需要公网可达地址（待陆叙确认挂哪），建议挂在现有后端服务同台机器，supervisor/systemd 守护；环境变量走部署平台注入。

## 关键行为约定

- **幂等**：按 `message_id` 去重（TTL 5 分钟），飞书超时重推不会重复建单。
- **先回 200 再异步处理**：事件回调内只做校验和分发，指令处理在线程里执行，避免飞书 3 秒超时重推。
- **群聊只响应 @机器人 的消息**，私聊全部响应。
- **token 缓存**：`tenant_access_token` 由 lark-oapi SDK 内置缓存管理，不每次现取。
- **重试**：Gitea 调用对 429（读 `Retry-After`）和 5xx 指数退避，最多 4 次；4xx 不重试直接报错。
