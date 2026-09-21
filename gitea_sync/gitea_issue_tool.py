#!/usr/bin/env python3
"""团队用 Gitea 工单查询/创建小工具（MYD-302 第二条线：便捷入口，不做聊天机器人）。

定位：团队成员快速查/建 docify/docify-agent 工单的命令行入口。
建单只写 Gitea——Multica 侧由 sync_issues.py 统一带进来，保持单一入口，避免双写不一致。

用法：
  python3 gitea_issue_tool.py list                      # 列出 open 工单
  python3 gitea_issue_tool.py list --label bug          # 按标签筛选（客户端二次过滤兜底）
  python3 gitea_issue_tool.py create --title "标题" --desc "描述"
  python3 gitea_issue_tool.py create --title "标题" --desc-file ./desc.md --labels bug,前端
  python3 gitea_issue_tool.py labels                    # 看仓库现有标签

凭证：环境变量 GITEA_TOKEN。
"""

import argparse
import sys

import gitea_client as gitea

log = gitea.log


def cmd_list(args) -> int:
    issues = gitea.list_open_issues(state="open")
    if args.label:
        want = args.label.strip()
        # 客户端二次过滤兜底：不依赖服务端 labels 参数行为，名称精确匹配
        issues = [i for i in issues
                  if any(l.get("name") == want for l in i.get("labels") or [])]
    if not issues:
        print("（无匹配工单）")
        return 0
    print(f"{'编号':>5}  {'标签':<12}  标题")
    print("-" * 70)
    for i in issues:
        labels = ",".join(l.get("name", "") for l in i.get("labels") or []) or "-"
        print(f"#{i['number']:>4}  {labels:<12}  {i['title']}")
        print(f"       {gitea.issue_html_url(i['number'])}")
    print(f"\n共 {len(issues)} 张 open 工单")
    return 0


def cmd_labels(args) -> int:
    labels = gitea.list_labels()
    if not labels:
        print("（仓库暂无标签，create 时用 --create-labels 可顺手建）")
        return 0
    for l in labels:
        print(f"  {l['id']:>4}  {l['name']}")
    return 0


def cmd_create(args) -> int:
    if args.desc_file:
        with open(args.desc_file, encoding="utf-8") as fh:
            desc = fh.read()
    else:
        desc = args.desc or ""

    label_ids: list[int] = []
    if args.labels:
        existing = {l["name"]: l["id"] for l in gitea.list_labels()}
        for name in [n.strip() for n in args.labels.split(",") if n.strip()]:
            if name in existing:
                label_ids.append(existing[name])
            elif args.create_labels:
                new = gitea.create_label(name)
                existing[name] = new["id"]
                label_ids.append(new["id"])
                log(f"标签不存在，已新建：{name} (id={new['id']})")
            else:
                log(f"❌ 标签「{name}」不存在。现有标签：{list(existing) or '（无）'}；"
                    f"或加 --create-labels 自动新建。")
                return 2

    issue = gitea.create_issue(args.title, desc, label_ids)
    print(f"✓ 已创建 #{issue['number']}：{issue['title']}")
    print(f"  {issue.get('html_url') or gitea.issue_html_url(issue['number'])}")
    print("  提示：Multica 侧由 sync_issues.py 定时/手动同步带入，无需重复建单。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="docify-agent Gitea 工单查询/创建小工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出 open 工单")
    p_list.add_argument("--label", default="", help="按标签名筛选（精确匹配）")
    p_list.set_defaults(fn=cmd_list)

    p_labels = sub.add_parser("labels", help="列出仓库现有标签")
    p_labels.set_defaults(fn=cmd_labels)

    p_create = sub.add_parser("create", help="创建新工单")
    p_create.add_argument("--title", required=True, help="工单标题")
    p_create.add_argument("--desc", default="", help="工单描述（markdown）")
    p_create.add_argument("--desc-file", default="", help="从文件读描述（优先于 --desc）")
    p_create.add_argument("--labels", default="", help="逗号分隔的标签名，如 bug,前端")
    p_create.add_argument("--create-labels", action="store_true",
                          help="标签不存在时自动新建（默认报错并列出现有标签）")
    p_create.set_defaults(fn=cmd_create)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except gitea.GiteaError as e:
        log(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
