#!/usr/bin/env python3
"""Gitea docify/docify-agent 工单 → Multica issue 同步脚本（MYD-302）。

口径（项目主管 2026-09-21 方案确认）：
  · 数据源：Gitea open 工单（type=issues，排除 PR）。
  · 去重三层（沿用 feishu_dispatch 已验证约定，查平台不查本地）：
      ① metadata 精确查：multica issue list --metadata gitea_issue=docify/docify-agent#<n>
         （前提同 feishu 线：--metadata 能查到已关闭的单，已修完的不会重建）；
      ② 描述溯源块兜底：建单时描述里原子写入 `gitea_issue` 标记，metadata 写丢的
         孤儿单按标记搜描述认领回来；
      ③ 本地 .sync_state.json 仅本轮辅助——autopilot 每次 checkout 会抹掉本地文件，
         不能当真相（主管 2026-09-21 明确：状态文件以平台侧标记为准、本地为辅）。
  · 前端识别：frontend_rules.json 配置化。label 命中 → 前端；无 label 按关键词，
    前后端信号同时命中算冲突 → 不自动指派（宁可少派不错派，主管口径）。
  · 指派：识别为前端的工单建单即派前端开发者（2026-09-22 链路调整：前端开发者修复
    提 PR → Gitea 专家验收 → 陆叙终审合并）；其余不派人留 todo 人工分拣。
  · 只做 Gitea → Multica 单向；Gitea 侧关闭不反向同步（二期再评估）。

用法：
  python3 sync_issues.py --dry-run     # 只读预演：会建哪些、会派哪些（默认，建议先跑这个）
  python3 sync_issues.py --execute     # 正式同步（dry-run 结果经主管确认后再用）
"""

import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import gitea_client as gitea

# ── 常量 ────────────────────────────────────────────────────────────────────
PROJECT_ID = "dd5c9377-979c-4ec1-bce0-00ea289175e5"          # docify 项目
# 前端工单建单即派目标：2026-09-22 起为前端开发者 agent（链路：前端开发者修复提 PR
# → Gitea 专家验收 → 陆叙终审合并）。目标写在 frontend_rules.json 的 frontend_assignee。

# metadata 键名是跨轮次契约，改名等于全量重建单，不要动。
MK_GITEA = "gitea_issue"          # 值：docify/docify-agent#<number>

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(HERE, "frontend_rules.json")
STATE_FILE = os.path.join(HERE, ".sync_state.json")

log = gitea.log


class MetadataWriteError(RuntimeError):
    """查重键没写进 issue metadata —— 那张单对查重隐形，必须中止本轮。"""


# ── 前端识别 ──────────────────────────────────────────────────────────────────
def load_rules() -> dict:
    with open(RULES_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _kw_hit(keyword: str, text: str) -> bool:
    """关键词匹配：纯 ASCII 词（api/500/UI/CSS 等）按词边界 + 大小写不敏感，
    避免 "500px"/"1500"/"build" 里的 "ui" 这类误命中；中文词保持子串匹配。"""
    if keyword.isascii():
        pat = rf"(?<![A-Za-z0-9]){re.escape(keyword.lower())}(?![A-Za-z0-9])"
        return re.search(pat, text.lower()) is not None
    return keyword in text


def classify(issue: dict, rules: dict) -> tuple[str, str]:
    """返回 (frontend|unknown, 依据)。冲突/无信号都归 unknown（不自动指派）。"""
    labels = [l.get("name", "") for l in issue.get("labels") or []]
    hit_labels = [l for l in labels if l in rules["frontend_labels"]]
    if hit_labels:
        return "frontend", f"命中前端标签: {'、'.join(hit_labels)}"

    text = f"{issue.get('title', '')}\n{issue.get('body') or ''}"
    fe = [k for k in rules["frontend_keywords"] if _kw_hit(k, text)]
    hard = [k for k in rules["backend_keywords_hard"] if _kw_hit(k, text)]
    soft = [k for k in rules["backend_keywords_soft"] if _kw_hit(k, text)]
    # 软后端词（越权/密码/api 类）只在没有前端信号时生效（主管 2026-09-22 确认：
    # #91 主题色、#94 提示文案是前端件，不该被这类词拦下）；硬后端词任何时候都算数。
    be = hard if fe else hard + soft
    if fe and not be:
        return "frontend", "命中前端关键词: " + "、".join(fe[:5])
    if fe and be:
        return "unknown", f"前后端信号冲突(前={fe[:3]} 后={be[:3]})，不自动指派"
    if be:
        return "unknown", "命中后端关键词: " + "、".join(be[:5])
    return "unknown", "无明确前后端信号，不自动指派"


# ── 平台侧查重（三层）────────────────────────────────────────────────────────
def gitea_key(number: int) -> str:
    return f"{gitea.OWNER}/{gitea.REPO}#{number}"


def _parse_json_stdout(stdout: str) -> dict:
    """multica CLI 的 stdout 前面可能带非 JSON 前缀，从第一个 { 起解析。"""
    s = stdout.find("{")
    return json.loads(stdout[s:]) if s >= 0 else {}


def query_issues_by_metadata(key: str, value: str) -> list[dict]:
    """multica issue list --metadata k=v 精确查。查不动 → 抛异常（宁可停也不重复建单）。"""
    r = subprocess.run(["multica", "issue", "list", "--metadata", f"{key}={value}",
                        "--limit", "20", "--output", "json"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"查重失败 {key}={value}: {r.stderr.strip()[:200]}")
    return _parse_json_stdout(r.stdout.strip()).get("issues") or []


_desc_index: list[dict] | None = None


def load_issue_descriptions() -> list[dict]:
    """全量 issue 描述索引（第三层兜底专用，翻页拉全，本轮只拉一次）。"""
    global _desc_index
    if _desc_index is not None:
        return _desc_index
    issues: list[dict] = []
    offset = 0
    while True:
        r = subprocess.run(["multica", "issue", "list", "--limit", "100",
                            "--offset", str(offset), "--output", "json"],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError(f"拉取 issue 全量索引失败(offset={offset}): {r.stderr.strip()[:200]}")
        page = _parse_json_stdout(r.stdout)
        batch = page.get("issues") or []
        issues += batch
        if not page.get("has_more") or not batch:
            break
        offset += len(batch)
        if offset > 20000:
            raise RuntimeError("issue 全量索引翻页超过 20000 条，疑似分页失效，停止")
    _desc_index = issues
    log(f"  已建单描述索引：{len(issues)} 张（第三层兜底用）")
    return _desc_index


def find_existing(number: int) -> tuple[dict | None, str]:
    """① metadata 精确查 → ② 描述溯源块兜底。返回 (issue|None, 命中键/原因)。"""
    key = gitea_key(number)
    hit = query_issues_by_metadata(MK_GITEA, key)
    if hit:
        return hit[0], f"metadata:{MK_GITEA}"
    # 描述兜底：匹配的是溯源块整行（`gitea_issue`：`...`），不是裸子串——
    # 讨论单正文会原样引用标记，裸搜会把讨论单当命中而静默漏建。
    needle = f"`{MK_GITEA}`：`{key}`"
    found = [i for i in load_issue_descriptions() if needle in (i.get("description") or "")]
    if found:
        iss = found[0]
        log(f"  ↺ #{number} metadata 查不到但描述里有标记 → 孤儿单 {iss.get('identifier')}，补写 metadata")
        try:
            set_issue_metadata(iss["id"], number)
        except MetadataWriteError as e:
            log(f"  ⚠️ 孤儿单 {iss.get('identifier')} metadata 补写仍失败：{e}（不致命，下轮还会兜住）")
        return iss, "描述溯源块"
    return None, "未命中"


# ── 本地 state（仅辅助）──────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"状态文件写入失败: {e}")


# ── 建单 ────────────────────────────────────────────────────────────────────
def build_description(issue: dict, cls: str, reason: str, n_attachments: int) -> str:
    number = issue["number"]
    url = gitea.issue_html_url(number)
    # 正文里的相对附件链接改写成 Gitea 绝对直链（兜底：即使附件上传失败也能溯源）
    body = gitea.absolutize_attachment_links((issue.get("body") or "").strip()) \
           or "_（Gitea 原文为空）_"
    labels = [l.get("name", "") for l in issue.get("labels") or []]
    lines = [
        f"> 由 Gitea 工单自动同步 · [打开原工单]({url}) · 更新于 {issue.get('updated_at', '')[:10]}",
        "",
        f"- **来源**：Gitea `{gitea.OWNER}/{gitea.REPO}#{number}`",
        f"- **Gitea 标签**：{'、'.join(labels) if labels else '（无）'}",
        f"- **前端判定**：{cls}（{reason}）",
        "",
        "## 工单原文",
        body,
    ]
    if n_attachments:
        lines += ["", "## 附件", f"- 原工单截图 {n_attachments} 张（见附件区，辅助调试修复）"]
    lines += [
        "",
        "---",
        "<!-- 自动同步溯源，请勿手改 -->",
        f"- 查重键 `{MK_GITEA}`：`{gitea_key(number)}`",
    ]
    return "\n".join(lines)


def set_issue_metadata(issue_id: str, number: int, retries: int = 3) -> None:
    """查重键写进 issue metadata，写完回读校验；写不进去抛错中止本轮。"""
    value = gitea_key(number)
    last = ""
    for attempt in range(retries):
        r = subprocess.run(["multica", "issue", "metadata", "set", issue_id,
                            "--key", MK_GITEA, "--value", value, "--type", "string"],
                           capture_output=True, text=True, timeout=120)
        bad = "" if r.returncode == 0 else r.stderr.strip()[:120]
        r = subprocess.run(["multica", "issue", "get", issue_id, "--output", "json"],
                           capture_output=True, text=True, timeout=120)
        got = {}
        if r.returncode == 0:
            got = _parse_json_stdout(r.stdout).get("metadata") or {}
        if not bad and str(got.get(MK_GITEA, "")) == value:
            return
        last = f"写入报错={bad or '无'} 回读值={got.get(MK_GITEA)!r}"
        if attempt < retries - 1:
            log(f"  metadata 写入未确认（{last}），{2 ** attempt}s 后重试")
            time.sleep(2 ** attempt)
    raise MetadataWriteError(f"{issue_id}（#{number}）查重键写入失败：{last}")


def create_issue(issue: dict, cls: str, reason: str, dry: bool,
                 attachments: list[str] | None = None,
                 assignee: dict | None = None) -> dict | None:
    """建 Multica issue；前端件建单即派 assignee（frontend_rules.json 配置）。dry 只打印。"""
    attachments = attachments or []
    number = issue["number"]
    title = ("[前端]" if cls == "frontend" else "") + issue["title"].strip()
    title = title[:60]
    assign = cls == "frontend" and assignee
    if dry:
        log(f"[dry] 建单《{title}》 指派={assignee['name'] if assign else '不派(留todo)'}（{reason}）"
            f" 附件={len(attachments)}")
        return None
    desc = build_description(issue, cls, reason, len(attachments))
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8",
                                     dir=os.getcwd()) as tf:
        tf.write(desc)
        desc_path = tf.name
    cmd = ["multica", "issue", "create", "--title", title,
           "--description-file", desc_path, "--project", PROJECT_ID,
           "--status", "todo", "--allow-duplicate", "--output", "json"]
    if assign:
        cmd += ["--assignee-id", assignee["id"]]
    for a in attachments:
        cmd += ["--attachment", a]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log(f"建单失败《{title}》: {r.stderr.strip()[:300]}")
            return None
        created = _parse_json_stdout(r.stdout.strip())
        issue_id = created.get("id") or created.get("identifier")
        if issue_id:
            set_issue_metadata(issue_id, number)
        return created if issue_id else None
    finally:
        try:
            os.unlink(desc_path)
        except OSError:
            pass


# ── 主流程 ───────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Gitea docify-agent 工单 → Multica issue 同步")
    ap.add_argument("--dry-run", action="store_true",
                    help="只读预演：输出会建哪些单、会派给谁（默认行为）")
    ap.add_argument("--execute", action="store_true",
                    help="正式同步（dry-run 结果经确认后再用）")
    args = ap.parse_args()
    if not args.execute:
        args.dry_run = True

    rules = load_rules()
    assignee_name = rules["frontend_assignee"]["name"]
    state = load_state()
    issues = gitea.list_open_issues(state="open")
    log(f"Gitea {gitea.OWNER}/{gitea.REPO} open 工单 {len(issues)} 张")
    tmpdir = tempfile.mkdtemp(prefix="gitea_att_", dir=os.getcwd())
    # 任何退出路径（正常/早退/异常）都清掉附件临时目录，不留在 cwd
    atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)

    skips: list[dict] = []
    fresh: list[dict] = []
    for issue in issues:
        number = issue["number"]
        cls, reason = classify(issue, rules)
        try:
            hit, key = find_existing(number)
        except RuntimeError as e:
            log(f"❌ 查重失败，停止：{e}")
            return 4
        if hit:
            skips.append({"number": number, "title": issue["title"], "key": key,
                          "issue": f"{hit.get('identifier')}({hit.get('status')})"})
            log(f"  跳过(已同步) #{number} 键={key} → {hit.get('identifier')}")
            continue
        # 附件：从正文抽 /attachments/<uuid> 链接下载（dry-run 也下载，验证附件行为）
        atts = []
        for i, path in enumerate(gitea.extract_attachment_paths(issue.get("body") or "")):
            local = gitea.download_attachment(path, tmpdir, f"i{number}_{i+1}")
            if local:
                atts.append(local)
        fresh.append({"issue": issue, "cls": cls, "reason": reason, "attachments": atts})
        # 本地辅助记录（真相在平台 metadata，这里只加速同轮判断/人工查看）
        state[gitea_key(number)] = {"status": "pending_create"}

    # ── 汇总输出 ──
    fe = [f for f in fresh if f["cls"] == "frontend"]
    un = [f for f in fresh if f["cls"] != "frontend"]
    print(f"\n{'='*70}\n【本轮将新建】 {len(fresh)} 张（前端派{assignee_name} {len(fe)} / 不指派 {len(un)}）\n{'='*70}")
    for f in fresh:
        i = f["issue"]
        print(f"  #{i['number']:>3} | {'→'+assignee_name if f['cls']=='frontend' else ' 留todo'} "
              f"| 附件{len(f['attachments'])} | {i['title'][:50]}")
        print(f"        判定: {f['reason']}")
    print(f"\n{'='*70}\n【本轮跳过(已同步)】 {len(skips)} 张\n{'='*70}")
    for s in skips:
        print(f"  #{s['number']:>3} | 命中键={s['key']} | 已存在={s['issue']}")
        print(f"        标题: {s['title'][:50]}")

    if args.dry_run:
        log(f"DRY-RUN 完成：将新建 {len(fresh)} 张（派{assignee_name} {len(fe)} 张），跳过 {len(skips)} 张。"
            f"确认无误后用 --execute 正式同步。")
        return 0

    # ── 正式建单 ──
    created: list[dict] = []
    for f in fresh:
        i = f["issue"]
        try:
            made = create_issue(i, f["cls"], f["reason"], dry=False,
                                attachments=f["attachments"],
                                assignee=rules.get("frontend_assignee"))
        except MetadataWriteError as e:
            log(f"❌ {e}，中止本轮（已建 {len(created)} 张）")
            save_state(state)
            return 5
        if not made:
            log(f"  建单失败，跳过：#{i['number']}")
            state[gitea_key(i["number"])] = {"status": "create_failed"}
            continue
        ident = made.get("identifier") or made.get("id")
        created.append({"identifier": ident, "number": i["number"],
                        "cls": f["cls"], "title": i["title"]})
        state[gitea_key(i["number"])] = {"status": "created", "issue": ident}
        log(f"  ✓ #{i['number']} → {ident}"
            + (f"，已派{assignee_name}" if f["cls"] == "frontend" else "，留 todo 待人工分拣"))
        time.sleep(0.3)
    state["last_sync_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_state(state)

    # 本轮新建清单（供主管批量分诊/核对）
    if created:
        log("── 本轮新建清单 ──")
        for c in created:
            log(f"  {c['identifier']} | #{c['number']} | {c['cls']} | {c['title'][:50]}")
    log(f"完成（真实同步）：新建 {len(created)} 张，跳过 {len(skips)} 张。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
