# 飞书「问题记录」表每日派单 / 后端巡检脚本（MYD-175）

读飞书电子表格 `Kq1nsadnQh8IzBt3MDWc1bCnnkc` 子表「问题记录」(`16e99d`，直接 token，无需 wiki 解析)，
做筛选 → 前后端综合判定（描述 + 截图）→ 前端派单 / 后端巡检消息。

与 MYD-16 的 `feishu_requirement_dispatcher.py`（Wiki 多维表 / 官网需求）**互不复用**，各自独立 autopilot。

## 运行模式

```bash
export FEISHU_APP_ID=... FEISHU_APP_SECRET=...

# 只读
python3 feishu_daily_dispatch.py --report            # 前端/后端/不确定三清单 + 命中统计
python3 feishu_daily_dispatch.py --dry-run           # 全链路自检（含 L/M 下拉选项可写性预检）

# 前端派单（真实建单 + 回写飞书）
python3 feishu_daily_dispatch.py --execute --limit 3 # 小样验证（需 L「丁」选项就绪）
python3 feishu_daily_dispatch.py --execute           # 全量

# 后端巡检（发消息进陆叙收件箱，不建单）
python3 feishu_daily_dispatch.py --backend-digest --execute
```

## 筛选与判定

- **系统（五环境，精确文本）**：`Docify-用户端` / `Docify官网` / `Docify.jp官网` / `Docify.jp-用户端` / `Docify-管理端`。
- **前后端判定**：结合**问题描述 + 问题截图**。sheets 内嵌图片(embed-image)用 `valueRenderOption=UnformattedValue` 渲染才拿得到 `fileToken`，再走 medias 下载作分析资料。
- **系统 → 仓库**：官网 / `Docify.jp官网` → `docify-main`；用户端 / `Docify.jp-用户端` → `docify-agent`(+`docify-web` 过渡期双写)；`Docify-管理端` → `docify-admin`。

## 前端派单（`--execute`）

筛选 M解决进展=`未解决`，建正式处理单的两类，按去重指纹（`系统|标题|描述前80字SHA1`）合并去重：

1. **简单前端件**（样式 / 前端代码路径）。
2. **测试直接指派给【丁志诚】且未解决的件**（L列，含遗留文本「丁」「丁？」）——无论前后端难易，只要还没建单就补建正式处理单跟踪调研+修复（陆叙 2026-08-13 追加）。

处理动作：建 issue → 派项目主管docify → 下载 E问题截图 + G环境 随 issue 上传 → 回写飞书 **L=丁志诚 + M=处理中** → 回读校验落成下拉选项。
前端单描述注入开发纪律：用私库 `env-examples/` 登录配置（别自造账号）；docify-web 的 `vendor` 由 `agent-pages:vendor-sync` 同步、**勿直接改 vendor 提交**；agent 相关在 docify-agent 项目改和测。

其余（未指派给丁的后端 / 不确定件）**不在此建单**。

## 后端巡检（`--backend-digest`）

不逐条建单。汇总 M解决进展∈{`未解决`,`处理中`}的**后端**问题，按进展分组、每条粗评「好不好改」（较好/中/难），
作为一条 **@陆叙 的评论**发进其 Multica 收件箱（陆叙口径：要私信/消息、不建单）。飞书 1:1 DM 因其 gmail 在通讯录解析不到 open_id 走不通，故用收件箱承载。

## L(开发) / M(解决进展) 是单选下拉

- L 合法选项实测 = `{丁志诚, 者俊, 闫超, 陈浩}`——**没有裸「丁」**。旧文本「丁」「丁？」是遗留脏值。
- 「已指派给丁」判定 = L ∈ `{丁, 丁？, 丁志诚}`。回写 L 必须写**合法选项值**（写自由文本会破坏下拉校验）。
- `--execute` 先预检：「丁」不在 L 选项里则**直接阻塞报错、不写任何数据**，需人工在飞书补选项或授权改写「丁志诚」（现用「丁志诚」）。
- M 合法选项含 `未解决`/`处理中`，回写「处理中」安全。

## 幂等

- 建单即回写 M=`处理中`，下轮「未解决」筛选自动排除。
- **autopilot 每次重新 checkout 本仓、`.daily_dispatch_state.json` 为空**，所以跨天幂等**真正靠 M=处理中 回写**，不能只依赖 state 文件。state 仅在单次运行内去重。

## Autopilot

- **测试BUG修复-前端派单(工作日)** `5ff11490` · cron `21 9 * * 1-5` Asia/Shanghai · `--execute`
- **后端问题每日检查单** `034e4ca3` · cron `31 9 * * *` · `--backend-digest --execute`（发消息不建单）
- 都先 `multica repo checkout` 本私库再 `cd feishu_dispatch` 运行；避开 MYD-16 的 9:13。
- 环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 运行时注入，不入库。
