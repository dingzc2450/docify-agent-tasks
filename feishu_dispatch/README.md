# 飞书「问题记录」表每日派单 / 后端巡检脚本（MYD-175）

读飞书电子表格 `Kq1nsadnQh8IzBt3MDWc1bCnnkc` 子表「问题记录」(`16e99d`，直接 token，无需 wiki 解析)，
做筛选 → 前后端综合判定（描述 + 截图）→ 前端派单 / 后端巡检消息。

与 MYD-16 的 `feishu_requirement_dispatcher.py`（Wiki 多维表 / 官网需求）**互不复用**，各自独立 autopilot。

## 运行模式

```bash
export FEISHU_APP_ID=... FEISHU_APP_SECRET=...

# 只读
python3 feishu_daily_dispatch.py --report            # 前端/后端/不确定三清单 + 命中统计 + 查重预演
python3 feishu_daily_dispatch.py --report --no-dedup-check   # 不查平台，纯离线看清单
python3 feishu_daily_dispatch.py --dry-run           # 全链路自检（含 L/M 下拉选项可写性预检）

# 前端派单（真实建单；默认不派人、不回写飞书 L/M）
python3 feishu_daily_dispatch.py --execute --limit 3 # 小样验证
python3 feishu_daily_dispatch.py --execute           # 全量
python3 feishu_daily_dispatch.py --execute --writeback-on-create  # 额外回写 L=丁志诚 + M=处理中（需 L「丁」选项就绪）

# 后端巡检（发消息进陆叙收件箱，不建单）
python3 feishu_daily_dispatch.py --backend-digest --execute
```

## 筛选与判定

- **系统（精确文本）**：`Docify-用户端` / `Docify官网` / `Docify.jp官网` / `Docify.jp-用户端` / `Docify-管理端` / `cn官网`。
  `cn官网` 是 MYD-272 才发现的变体（中国站官网，等同 `Docify官网` → `docify-main`），漏了它会整行读不到。
- **行数不写死**：运行时查 grid `row_count`（2026-08 是 231、09-07 已 316），写死会漏读尾部新行。
- **前后端判定**：结合**问题描述 + 问题截图**。sheets 内嵌图片(embed-image)用 `valueRenderOption=UnformattedValue` 渲染才拿得到 `fileToken`——`ToString`/`FormattedValue` 会把 token 抹掉，v2 指纹就算不出来。
- **系统 → 仓库**：官网 / `Docify.jp官网` / `cn官网` → `docify-main`；用户端 / `Docify.jp-用户端` → `docify-agent`(+`docify-web` 过渡期双写)；`Docify-管理端` → `docify-admin`。
- **截图落地按 magic number 定扩展名**（原表 PNG/JPEG 混用，文件名不可信）；下载时 `Authorization` 头必须真的带上 token，否则接口返 400 并把 JSON 错误写进文件——脚本会按 magic number 识破并丢弃这种坏图。

## 查重（MYD-272 定稿，替换了旧的「系统|标题|描述前80字」本地指纹）

键写在 **issue metadata** 上，用 `multica issue list --metadata k=v` 精确查——不是查本地文件：

> **隐含前提（项目主管 2026-09-07 实测确认）：`--metadata` 查询能查到已关闭的单**，done / cancelled 都精确返回（拿 MYD-165 done、MYD-11 cancelled 验过）。整套查重都压在这条上：如果哪天平台改成默认只查开放单，所有已修完的历史问题会瞬间变成「没建过」被重复建一遍。改查询参数前先重验。

| 键 | 取值 | 角色 |
| --- | --- | --- |
| `feishu_fingerprint_v2` | `sha1(系统\|菜单\|问题分类\|问题描述\|更新日期\|截图fileToken列表)` | **主键**，实测全表唯一 |
| `feishu_fingerprint` | `sha1(系统\|菜单\|问题分类\|问题描述\|更新日期)` | 次级键 / 兜底 |
| `feishu_row` | `<表token>!<sheetId>#<行号>` | **仅提示，会漂移** |
| `feishu_row_verified` | bool | 建单当刻核对过行号 |

- **v2 优先、v1 必须兜底**：截图被重传会换 fileToken → v2 假阴性，只认 v2 会重复建单。
- **无截图的行 v2 不可算**（原表约 19 行）→ 自动降级为只用 v1。
- **v1 相撞的行禁用 v1 兜底**：全表跑一遍 v1 找相撞指纹（实测「会话区」行 262/264/265/266 与 267/268 同系统同菜单同分类且描述全空）。这些行**只认 v2**；若又没截图 → 两个键都定位不了，不自动建单，进人工清单。相撞组是**每次运行动态算的，不写死行号**——行号会漂移（行 158 在 5 天内指向过两条不同记录）。
- **跳过必须留痕**：每轮结束打印「本轮跳过明细」——哪些行、命中哪个键、撞上哪张单。静默跳过会让漏建和重复建都查不出来。
- 建单描述末尾还写一份人类可读的溯源块（两个指纹 + 行号），与 metadata 双写。

## 优先级（机械判定，不取原表 J 列）

原表 J【优先级】填写率低、口径不一（描述全空的行也标「高」），只作参考写进描述。脚本按三步判：

1. **基准档**：描述为空 → `medium`（判不了，取中档并注明需向测试补复现步骤）；分类 ∈ UI/样式/文案/多语言/交互 → `low`；分类 ∈ 功能/性能/越权等 → `medium`；分类为空才用关键词猜。
   分类填了就以分类为准——行 232/249/283 是「功能问题」但描述里带「文案」「翻译」，靠关键词会被误降成 low。
2. **上调**：整页不可用 / 数据取不到（白屏、打不开、崩溃、无法保存、500…）→ 任何档上调一档；仅影响范围为全局/整页（导航栏、与设计不符、布局错乱）→ 只把 UI 类的 `low` 抬到 `medium`，不推到 `high`。
3. **封顶**：dev/测试环境的行封顶 `medium`（dev 阶段不出 high）。注意 `Docify.jp官网` / `cn官网` 是**生产域名不是 dev**，不能一刀切封顶——行 233（切多语言整页白屏）就是靠这条留在 `high`。
   **环境列为空按 dev 处理**（项目主管 2026-09-07）：这张表大多数行本来就是在测试环境填的，空值更可能是「懒得填」而不是「生产」；判错方向要选代价小的一边——误封顶只是少一档、分诊能提回来，误按生产放行则把 dev 的问题推成 high。

该口径对 MYD-274~289 那 16 张人工复核过的单 **100% 复现**（`assess_priority` 改动后请重跑核对；环境为空封顶这条上线后已重跑，仍 16/16）。

## 前端派单（`--execute`）

筛选 M解决进展=`未解决`，建正式处理单的两类，按 v2（无 v2 退 v1）同轮去重：

1. **简单前端件**（样式 / 前端代码路径），**且 L 列没写别人的名字**——L 里 闫超 107 行、者俊 11、陈浩 3、冯志康 2，不加这条会把别人名下的 bug 又派给丁一遍。
2. **测试直接指派给【丁志诚】且未解决的件**（L列，含遗留文本「丁」「丁？」）——无论前后端难易，只要还没建单就补建正式处理单跟踪调研+修复（陆叙 2026-08-13 追加）。

处理动作：查重（v2→v1）→ 建 issue → 写 metadata 查重键 → 下载 E问题截图 + G环境 随 issue 上传。
**建单不派人**（项目主管 2026-09-07）：`--assignee-id` + `--status todo` 的组合会每建一张单就点起一次被指派 agent 的 run，和「一批全落地后做一轮集中分诊」的约定冲突。分诊统一人工做一轮。
**原表 L/M 不回写**（默认 `--writeback-on-create` 关闭）：建单 ≠ 有人开工，L/M 列是测试在看的信息源，回写「处理中」会塞进去一个不准的状态。M 列的回写应跟着真实指派走，分诊派人时再回写。
前端单描述注入开发纪律：用私库 `env-examples/` 登录配置（别自造账号）；docify-web 的 `vendor` 由 `agent-pages:vendor-sync` 同步、**勿直接改 vendor 提交**；agent 相关在 docify-agent 项目改和测。

其余（未指派给丁的后端 / 不确定件 / 别人名下的件）**不在此建单**。

## 后端巡检（`--backend-digest`）

不逐条建单。汇总 M解决进展∈{`未解决`,`处理中`}的**后端**问题，按进展分组、每条粗评「好不好改」（较好/中/难），
作为一条 **@陆叙 的评论**发进其 Multica 收件箱（陆叙口径：要私信/消息、不建单）。飞书 1:1 DM 因其 gmail 在通讯录解析不到 open_id 走不通，故用收件箱承载。

## L(开发) / M(解决进展) 是单选下拉

- L 合法选项实测 = `{丁志诚, 者俊, 闫超, 陈浩}`——**没有裸「丁」**。旧文本「丁」「丁？」是遗留脏值。
- 「已指派给丁」判定 = L ∈ `{丁, 丁？, 丁志诚}`。回写 L 必须写**合法选项值**（写自由文本会破坏下拉校验）。
- 只有带 `--writeback-on-create` 时才回写、才预检 L 选项：「丁」不在 L 选项里则**直接阻塞报错、不写任何数据**（现用「丁志诚」）。默认不回写，这项预检也就不拦路。
- M 合法选项含 `未解决`/`处理中`，回写「处理中」安全——但**默认不写**，见上「建单不回写原表」。

## 幂等

- **metadata 查重键是唯一真相**（v2 主键 + v1 兜底）。`.daily_dispatch_state.json` 只在**单次运行内**兜底；autopilot 每次重新 checkout 本仓，state 文件必为空。
- 建单默认不再回写 M=`处理中`，所以「下轮未解决筛选自动排除」这层保险**没有了**——跨轮次幂等 100% 压在 metadata 上，写漏一张 = 那张单永久隐形、下轮必重复建。
- 因此 `set_issue_metadata` 是硬要求，不是尽力而为：写完**回读校验**、失败**重试 3 次**（指数退避）；仍失败则把该单记进 `.metadata_backfill_needed.json` 并**中止本轮**（平台没有 `issue delete`，删单回滚这条路走不通）。
- **下轮启动先读这个失败清单，非空就阻塞报警**：`--execute` 直接拒跑（返回 6），人工补齐 metadata、删掉该文件后才继续。
- 查重调用本身失败则**直接停止**，宁可不建也不重复建。
- 历史教训：MYD-273 之前有 59 张历史单没有 metadata 键，对查重完全隐形（其中 36 张挂在 in_review——最可能被打回的状态），靠全量回填才补上。这就是为什么现在写不进去要中止。

## Autopilot

- **测试BUG修复-前端派单(工作日)** `5ff11490` · cron `21 9 * * 1-5` Asia/Shanghai · `--execute`
- **后端问题每日检查单** `034e4ca3` · cron `31 9 * * *` · `--backend-digest --execute`（发消息不建单）
- 都先 `multica repo checkout` 本私库再 `cd feishu_dispatch` 运行；避开 MYD-16 的 9:13。
- 环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 运行时注入，不入库。
