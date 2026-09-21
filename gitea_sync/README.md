# gitea_sync — Gitea 工单同步 & 查询/建单工具

来源需求：MYD-302。两条能力：

1. **`sync_issues.py`** — 把 Gitea `docify/docify-agent` 的 open 工单同步成 Multica issue，
   识别为前端的自动指派给陆叙（zhicheng.ding）。
2. **`gitea_issue_tool.py`** — 团队成员快速查/建 Gitea 工单的命令行小工具。
   建单只写 Gitea，Multica 侧由同步脚本统一带入，保持单一入口。

## 前置

```bash
export GITEA_TOKEN=<你的 Gitea 访问令牌>   # 只走环境变量，不入库
python3 --version                          # >= 3.10，无第三方依赖（标准库 urllib）
# sync_issues.py 还需要 multica CLI 已登录（建 Multica issue 用）
```

## 同步脚本 sync_issues.py

```bash
cd gitea_sync
python3 sync_issues.py --dry-run    # 只读预演：会建哪些单、会派给谁（先跑这个）
python3 sync_issues.py --execute    # 正式同步（dry-run 结果确认后再跑）
```

口径：

- **数据源**：Gitea open 工单（`type=issues`，排除 PR）。
- **去重三层**（沿用 `feishu_dispatch` 已验证约定，查平台不查本地）：
  1. issue metadata 精确查：`multica issue list --metadata gitea_issue=docify/docify-agent#<n>`；
  2. 描述溯源块兜底：建单时描述里原子写入查重键，metadata 写丢的孤儿单按描述认领；
  3. 本地 `.sync_state.json` 仅辅助（autopilot 每次 checkout 会抹掉本地文件，不能当真相）。
- **前端识别**（`frontend_rules.json` 配置化，改清单不改代码）：
  - Gitea label 命中 `frontend_labels` → 前端；
  - 无 label 时按关键词匹配标题+正文：只中前端词 → 前端；
  - 前后端信号同时命中 / 无信号 → **不自动指派**（宁可少派不错派），留 todo 人工分拣。
- **指派**：前端工单建单即派陆叙；其余不派人。
- **单向**：Gitea → Multica；Gitea 侧关闭不反向同步（二期再评估）。

## 查询/建单工具 gitea_issue_tool.py

```bash
cd gitea_sync
python3 gitea_issue_tool.py list                 # 列出 open 工单
python3 gitea_issue_tool.py list --label bug     # 按标签筛选
python3 gitea_issue_tool.py labels               # 看仓库现有标签
python3 gitea_issue_tool.py create --title "定时任务保存报500" \
    --desc "复现步骤：……" --labels bug            # 建单
python3 gitea_issue_tool.py create --title "..." --desc-file ./desc.md \
    --labels bug,前端 --create-labels             # 标签不存在时自动新建
```

建单成功后无需手动去 Multica 建单——同步脚本会按查重键自动带入。

## 文件

| 文件 | 说明 |
|---|---|
| `gitea_client.py` | Gitea REST API 封装（429/5xx 重试，token 走环境变量） |
| `sync_issues.py` | 同步脚本（dry-run 默认） |
| `gitea_issue_tool.py` | 团队查询/建单 CLI |
| `frontend_rules.json` | 前端识别规则（标签/关键词清单） |
| `.sync_state.json` | 本地辅助状态（gitignore，非真相） |
