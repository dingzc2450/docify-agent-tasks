# 飞书「问题记录」表每日派单脚本（MYD-175）

读飞书电子表格 `Kq1nsadnQh8IzBt3MDWc1bCnnkc` 子表「问题记录」(`16e99d`)，
筛选 → 前后端综合判定（描述 + 截图）→ 分类建单/分派 → 回写 L/M。

与 MYD-16 的 `feishu_requirement_dispatcher.py`（Wiki 多维表 / 官网需求）**互不复用**，
两条链路各自独立 autopilot。

## 运行

```bash
export FEISHU_APP_ID=... FEISHU_APP_SECRET=...
python3 feishu_daily_dispatch.py --report            # 只读：前端/后端/不确定三清单 + 统计
python3 feishu_daily_dispatch.py --dry-run           # 全链路自检（含 L/M 下拉选项可写性预检）
python3 feishu_daily_dispatch.py --execute --limit 3 # 真实回写小样验证（需 L「丁」选项就绪）
python3 feishu_daily_dispatch.py --execute           # 全量真实建单/回写
```

## 关键：L(开发) / M(解决进展) 已改为**单选下拉**

- L 合法选项实测 = `{丁志诚, 者俊, 闫超, 陈浩}`——**没有裸「丁」**。旧文本「丁」「丁？」是遗留脏值。
- 「已分配给丁」判定 = L ∈ `{丁, 丁？, 丁志诚}`（含遗留文本 + 正式选项）。
- 回写 L 必须写**合法选项值**，不能写自由文本（会破坏下拉校验）。`--execute` 会先预检选项，
  「丁」不在选项里则直接阻塞报错、不写任何数据，需人工在飞书补选项或授权改写「丁志诚」。
- M 合法选项含 `未解决`/`处理中`，回写「处理中」安全。

## 幂等

去重指纹 `系统|标题|描述前80字SHA1`，不绑行号；state 存 `.daily_dispatch_state.json`
（本目录不提交 state，运行态数据）。

## Autopilot

工作日（周一至周五）Asia/Shanghai，避开 MYD-16 的 9:13。部署时脚本需落在持久目录
（如 `~/feishu-dispatch/`）或由 autopilot 先 checkout 本仓再运行。
