# 调试环境变量样例（env-examples）

收录 docify 前端本地自动化调试用的两份环境变量配置，方便 docify-agent 和 docify-web 快速起本地联调环境。

> ⚠️ 本目录含真实测试账号密码与 `AUTH_SECRET`，仅限私库内部使用，切勿外传或提交到公开仓库。

## 文件说明

### `docify-agent.env.local`

docify-agent 的 dev-shell 使用（对应仓库里的 `.env.local`），配合 Fastify 代理（`scripts/proxy-server.cjs`）本地起前端联调。

- 前端 API 全部指向本机代理（`localhost:3016`），浏览器不直连远端，避免 CORS / cookie 域问题。
- 代理独立读取此文件里的账号，调用 Java 后端 `/oauth2/token` 换真实 token，凭据不进浏览器 bundle。

### `docify-web.env`

docify-web 前端壳使用，代理端口为 `3077`。

## 关键约定

- **切换本地 / 线上后端只改 `PROXY_DOCIFY_NEXT_UPSTREAM` 一行**，然后重启 proxy：
  - 线上 DEV：`https://dev.docify.jp/docify-next`
  - 本地后端：`http://localhost:8502`（`start_backend.ps1` 启动后监听 8502）
- `DEV_BEARER_TOKEN`（仅 agent 配置）：只在 upstream 指向 `http://localhost:8502` 时才可填静态 token，指向线上 DEV 时**必须留空**，否则代理会用假 token 覆盖 OAuth JWT，线上后端一律返回 401。
- `AUTH_SECRET` 一值两用：既是 `oauth2/token` 的 Basic 客户端凭据头，也是 next-auth 的签名 secret，**必须保持这个 Basic 串**，填占位值会导致登录 401 或 signOut MissingSecret 500。

各变量的详细说明见文件内注释，token 有效范围、留空规则等已随文件原样保留。

## 用法

复制到对应项目并按需去掉扩展名即可，例如：

```bash
# docify-agent
cp env-examples/docify-agent.env.local /path/to/docify-agent/.env.local

# docify-web
cp env-examples/docify-web.env /path/to/docify-web/.env.local
```
