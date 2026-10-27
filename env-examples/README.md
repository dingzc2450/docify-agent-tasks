# 调试环境变量样例（env-examples）

收录 docify 前端本地自动化调试用的环境变量配置，方便 docify-agent 快速起本地联调环境。

> 2026-10 起 docify-web / docify-admin 已融合进 docify-agent，`docify-web.env` 仅作历史保留，新联调一律用 `docify-agent.env.local`。

> ⚠️ 本目录含真实测试账号密码与 `AUTH_SECRET`，仅限私库内部使用，切勿外传或提交到公开仓库。

## 文件说明

### `docify-agent.env.local`

按 MYD-364 附件 `.env.local` 更新，供 docify-agent 的 app/web 与 app/admin 共用（实际仓库目录为 `apps/web`、`apps/admin`）。两端连接同一 Java 后端，使用同一套登录账号与后端参数。

- 本地 dev 由 `packages/dev-toolkit` 单进程 launcher 承载 Next、代理和自动登录；web 默认端口 `3015`，admin 默认端口 `3010`。
- 附件保留 web 的地址配置；用于 admin 时，`NEXT_PUBLIC_BASE_URL`、`NEXT_PUBLIC_API_URL` 中指向 launcher 的绝对地址需随实际端口调整。`PROXY_PORT` 默认注释，使用各端默认端口。
- dev 页面请求走 launcher 同源相对路径；`NEXT_PUBLIC_BASE_URL` 用于 embed 预览片段，`NEXT_PUBLIC_API_URL` 供资源代理等 server 端场景使用。
- launcher 使用服务端的 `AUTH_USERNAME`、`AUTH_PASSWORD`、`AUTH_SECRET` 调用 Java 后端 `/oauth2/token`，凭据不进入浏览器 bundle。

### `docify-web.env`（已废弃，仅历史保留）

原 docify-web 前端壳使用，代理端口为 `3077`。docify-web 已并入 docify-agent，本文件不再维护，请勿用于新环境。

## 关键约定

- Java 后端（`/api`、`/oauth2` 与自动登录）的上游由 `BASE_URL` 指定。
- Agent 后端上游由 `PROXY_DOCIFY_NEXT_UPSTREAM` 指定；本地 Agent 联调使用 `http://localhost:8502`。修改后重启 dev launcher。
- `PROXY_DOCIFY_NEXT_CLIENT_IP` 仅对本地 Agent upstream 生效，用于地区敏感链路调试，默认注释。
- 保留附件中的功能开关、图片白名单、Office 预览地址和可选认证变量；不要直接沿用旧独立代理配置。
- 变量加载顺序为 shell 环境 > `.env.local` > `.env.development` > `.env`。完整清单及消费方说明见 docify-agent 根目录 `ENV.md` 与各应用的 `.env.example`。

## 用法

将配置复制到需要调试的应用目录：

```bash
# app/web
cp env-examples/docify-agent.env.local /path/to/docify-agent/apps/web/.env.local

# app/admin（复制后按实际 launcher 端口调整上述同源绝对地址）
cp env-examples/docify-agent.env.local /path/to/docify-agent/apps/admin/.env.local
```
