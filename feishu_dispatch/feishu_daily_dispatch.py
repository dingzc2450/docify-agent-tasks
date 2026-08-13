#!/usr/bin/env python3
"""飞书「问题记录」表每日拉取 → 前后端综合判定 → 建单分配 → 回写。

数据源与 MYD-16 的官网需求链路**不是同一张表**：
  · 本脚本：电子表格 https://blxv28dmue.feishu.cn/sheets/Kq1nsadnQh8IzBt3MDWc1bCnnkc
    子表「问题记录」sheetId=16e99d（直接 token，无需 wiki 解析）。
  · MYD-16：Wiki 多维表 PYTwwi0tnimYJbkrsc1cLWyVnRg（官网需求），两条链路互不复用。

沿用 MYD-62 已锁定的规则（不推翻）：
  · 筛选：A【系统】∈{Docify-用户端, Docify官网}（精确文本）且 M【解决进展】=未解决；
    已分配（L 标【丁】/【丁？】全角）不豁免。
  · 列结构：L=开发（标【丁】写这列）、M=解决进展。
  · 去重指纹：系统 | 标题 | 描述前80字SHA1，不绑行号（行号会漂移）；
    state 存本地 map（row_key→issue_id）。已存在 issue→重置 todo 交陆叙人工；未存在→分类建单。
  · 难易口径：简单=样式调整/代码路径修复/后端简单改字段·加字段·调整某CRUD；
    难=新增功能/操作路径长/无法复现；无法归类→标 uncertain 交人工。
  · 两条链路：A 简单→正式处理单（assign 项目主管、标 L=丁、进解决后回写 M=处理中）；
    B 难/不确定→分析问题单（不派人、L 不标、M 不动，等陆叙授权）。
  · 附件：E 问题截图 + G 环境 都下载并同步进 issue。
  · 系统→仓库：官网→docify-main；用户端前端→docify-agent(+过渡期同步 docify-web)；
    用户端后端→docify-agent。建单描述模板写明「99% 基于 develop 起支线」。

本次强化（陆叙 2026-08-12）：
  1. 前端判定不只看关键词，**结合「问题描述」+「问题截图」综合判断**。截图经
     sheets values UnformattedValue 渲染可拿到 fileToken → medias 下载，作为分析资料。
  2. 判定不确定（无法明确归前后端）的问题**单独汇总成不确定清单**，不硬派。
  3. 先出报告：--report/--dry-run 只读，产出前端/后端/不确定三张清单交陆叙复核；
     复核后再放开真实建单/回写，最后才建每日 autopilot。

环境变量：FEISHU_APP_ID / FEISHU_APP_SECRET

用法：
  python3 feishu_daily_dispatch.py --report            # 只读，产出三类清单 + 命中统计（默认下载截图）
  python3 feishu_daily_dispatch.py --report --no-download   # 只读，跳过截图下载（更快）
  python3 feishu_daily_dispatch.py --dry-run           # 全链路自检（筛选/仓库映射/难易/前后端），不建单不写回
  python3 feishu_daily_dispatch.py                     # 正式执行（复核放行后才用）
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://open.feishu.cn/open-apis"

# ── 数据源 ──────────────────────────────────────────────────────────────────
SHEET_TOKEN = "Kq1nsadnQh8IzBt3MDWc1bCnnkc"   # 电子表格 token（直接可用，非 wiki）
SHEET_ID = "16e99d"                            # 子表「问题记录」
SHEET_URL = f"https://blxv28dmue.feishu.cn/sheets/{SHEET_TOKEN}"
MAX_ROW = 231                                  # grid row_count（探查所得，留足冗余）

# ── 列索引（0 基，实测表头对齐）────────────────────────────────────────────
# A系统 B菜单 C问题分类 D问题描述 E问题截图 F测试内容/指令 G环境 H账号 I(空) J优先级
# K更新日期 L开发 M解决进展 N解决备注
COL_SYS, COL_MENU, COL_CAT, COL_DESC, COL_SHOT = 0, 1, 2, 3, 4
COL_TESTS, COL_ENV, COL_ACCT, COL_PRI = 5, 6, 7, 9
COL_DATE, COL_DEV, COL_PROGRESS, COL_REMARK = 10, 11, 12, 13

# ── 锁定的筛选口径 ─────────────────────────────────────────────────────────
# 陆叙 2026-08-13 澄清 .jp/管理端 都是不同域名/环境的测试，一并纳入：
#   Docify官网 / Docify.jp官网      → jp/主域名官网，前端为主 → docify-main
#   Docify-用户端 / Docify.jp-用户端 → docify-agent(+web) 前后端
#   Docify-管理端                    → docify-admin 前后端
TARGET_SYSTEMS = {"Docify-用户端", "Docify官网",
                  "Docify.jp官网", "Docify.jp-用户端", "Docify-管理端"}
UNRESOLVED = "未解决"
IN_PROGRESS = "处理中"   # M 回写值（M 现为下拉，「处理中」是合法选项，实测确认）
# 【开发】列已标不豁免——仍纳入筛选，靠去重指纹 + 已存在 issue 判定收敛。
# L(开发) 列 2026-08-12 起由自由文本改为**单选下拉**，合法选项实测=
#   {丁志诚, 者俊, 闫超, 陈浩}——注意没有裸「丁」！旧文本「丁」「丁？」是遗留脏值。
# 「分配给丁」的判定 = L ∈ 下面这些值（含遗留文本 + 正式选项）。
DING_VALUES = {"丁", "丁？", "丁志诚"}
# 回写 L 时的目标：优先裸「丁」（若被加进选项），否则用已存在的合法选项「丁志诚」。
# 二者都不在选项里 → 阻塞，不瞎写文本（会破坏下拉校验）。
DING_WRITE_PREFERENCE = ["丁", "丁志诚"]


class BlockedError(RuntimeError):
    """遇到需要陆叙决策的阻塞（如选项不存在），不硬写。"""

# ── 系统 → 仓库 ────────────────────────────────────────────────────────────
def repos_for(system: str, category: str) -> list[str]:
    """系统 + 前后端归属 → 目标仓库。"""
    if system in ("Docify官网", "Docify.jp官网"):
        return ["docify-main"]                   # 官网(含 jp)，前端为主
    if system == "Docify-管理端":
        return ["docify-admin"]                  # 管理端前后端
    # Docify-用户端 / Docify.jp-用户端（jp 只是域名/环境差异，仓库同用户端）
    if category == "frontend":
        return ["docify-agent", "docify-web"]   # 过渡期前端双写
    return ["docify-agent"]                      # 后端 / 不确定默认落 agent

PROJECT_ID = "dd5c9377-979c-4ec1-bce0-00ea289175e5"

# 项目主管 / 开发 agent（沿用 MYD-16 已解析的 UUID；本轮报告不实际分配）
AGENT_FRONTEND = "9fe23881-15cb-4b24-94f4-415a165f81fb"   # 前端开发者
AGENT_BACKEND = "576803bf-8bb6-42c5-bbdb-98c1f0932897"    # 后端架构师
AGENT_GENERAL = "7206de6c-ea56-4c50-a746-023a9b76fd3e"    # 通用开发者
AGENT_FULLSTACK = "a04834ef-c21a-4559-95e1-46e7a859e04c"  # 全栈开发者

# 建单描述统一提示（99% 基于 develop 起支线）
BRANCH_HINT = "> 约定：99% 情况基于 `develop` 起支线开发（除非任务另有说明）。"

# 前端本地联调 & 提交纪律（陆叙 2026-08-13 明确），按目标仓库条件化写进派单描述：
DEV_ENV_HINT_COMMON = (
    "> 🔧 本地联调环境变量：私库 `docify-agent-tasks/env-examples/` 有 docify-web 与 "
    "docify-agent 的前端自动化登录配置（含测试账号/代理），直接复制到对应项目 `.env.local` 用即可，"
    "别自己造账号。"
)
DEV_ENV_HINT_WEB = (
    "> ⚠️ docify-web 的 `vendor` 是由根目录脚本 `agent-pages:vendor-sync` 同步得到的，"
    "**不要直接改 vendor 目录并提交**——改动源头、跑同步脚本重新生成。"
)
DEV_ENV_HINT_AGENT = (
    "> ⚠️ agent 相关内容一律在 **docify-agent** 项目里改和测（不要在 docify-web 侧改 agent 逻辑）。"
)


# ── 分类关键词（初判，最终结合截图综合）─────────────────────────────────────
FE_CATS = {"UI"}                                     # 问题分类里明确前端的
FE_KW = ["样式", "布局", "显示", "页面", "颜色", "主题色", "文案", "对齐", "排版",
         "字体", "按钮", "点击", "交互", "弹窗", "滚动", "下拉", "选中", "名称", "图标",
         "居中", "遮挡", "错位", "闪烁", "白屏", "空白", "换行", "溢出", "响应式", "适配"]
BE_CATS = {"性能问题", "性能", "越权问题", "PDF 生成异常", "文档生成", "上下文问题",
           "响应中断", "对话意图理解错误", "对话过程"}
BE_KW = ["接口", "api", "报错", "execution failed", "500", "502", "超时", "越权", "权限",
         "计费", "点数", "用量", "数据库", "同步", "定时任务", "连接器", "邮件", "token",
         "上下文", "响应中断", "性能", "慢", "崩溃", "服务", "后端", "登录失败", "验证码",
         "手机号", "注册", "密码", "鉴权", "数据不一致", "丢失", "生成失败", "解析"]


def classify(desc: str, category: str, menu: str, tests: str,
             n_shots: int) -> tuple[str, str, str]:
    """综合初判前后端 + 难易。返回 (category∈{frontend,backend,uncertain}, difficulty, reason)。
    关键词只给初信号；信号弱/冲突 → uncertain（留给截图视觉复核 / 人工）。"""
    text = f"{desc}\n{tests}".lower()
    cat = (category or "").strip()

    fe_hit = [k for k in FE_KW if k.lower() in text] + ([f"分类={cat}"] if cat in FE_CATS else [])
    be_hit = [k for k in BE_KW if k.lower() in text] + ([f"分类={cat}"] if cat in BE_CATS else [])

    # 难易
    if any(w in text for w in ["无法复现", "偶现", "重现不了", "新增", "增加功能", "希望支持", "建议增加"]):
        difficulty = "hard"
    elif fe_hit and not be_hit:
        difficulty = "easy"   # 纯样式/前端路径通常简单
    else:
        difficulty = "medium"

    # 前后端归属
    if fe_hit and not be_hit:
        return "frontend", difficulty, "命中前端信号: " + "、".join(fe_hit[:4])
    if be_hit and not fe_hit:
        return "backend", difficulty, "命中后端信号: " + "、".join(be_hit[:4])
    if fe_hit and be_hit:
        return "uncertain", difficulty, (f"前后端信号冲突(前={fe_hit[:2]} 后={be_hit[:2]})"
                                         f"，需结合{n_shots}张截图复核")
    # 无关键词命中（多为「功能问题」大类）→ 交截图/人工
    return "uncertain", difficulty, (f"描述无明确前后端信号（分类={cat or '空'}）"
                                     f"，需结合{n_shots}张截图复核")


# ── 基础设施（沿用 MYD-16 已验证实现）────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _request(method: str, path: str, token: str | None = None, body: dict | None = None,
             max_retries: int = 4) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
            if payload.get("code", 0) != 0:
                raise RuntimeError(f"API 错误 code={payload.get('code')} msg={payload.get('msg')}")
            return payload
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                w = 2 ** attempt
                log(f"HTTP {e.code}，{w}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(w)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < max_retries - 1:
                w = 2 ** attempt
                log(f"网络异常 {e}，{w}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(w)
                continue
            raise
    raise RuntimeError(f"请求失败，已重试 {max_retries} 次: {last}")


class TokenManager:
    def __init__(self, app_id: str, app_secret: str):
        self._id, self._secret = app_id, app_secret
        self._token, self._exp = "", 0.0

    def get(self) -> str:
        if self._token and time.time() < self._exp:
            return self._token
        p = _request("POST", "/auth/v3/tenant_access_token/internal",
                     body={"app_id": self._id, "app_secret": self._secret})
        self._token = p["tenant_access_token"]
        self._exp = time.time() + (p["expire"] - 300)
        return self._token


# ── sheets 读取 ────────────────────────────────────────────────────────────
def fetch_grid(tokens: TokenManager) -> list[list]:
    """拉整块 A1:N{MAX_ROW}，UnformattedValue 渲染——文本正常返回，
    内嵌图片(embed-image)单元格会带 fileToken，可下载。"""
    rng = f"{SHEET_ID}!A1:N{MAX_ROW}"
    p = _request("GET",
                 f"/sheets/v2/spreadsheets/{SHEET_TOKEN}/values_batch_get"
                 f"?ranges={rng}&valueRenderOption=UnformattedValue",
                 token=tokens.get())
    return p["data"]["valueRanges"][0]["values"]


def cell_text(v) -> str:
    """把单元格值渲染成纯文本（跳过图片段）。"""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts = []
        for seg in v:
            if isinstance(seg, dict):
                if seg.get("type") == "embed-image":
                    continue
                parts.append(seg.get("text") or seg.get("link") or seg.get("name") or "")
            else:
                parts.append(str(seg))
        return "".join(p for p in parts if p).strip()
    if isinstance(v, dict):
        return v.get("text") or v.get("name") or ""
    return str(v)


def cell_image_tokens(v) -> list[str]:
    """从单元格里抽 embed-image 的 fileToken 列表。"""
    out = []
    if isinstance(v, list):
        for seg in v:
            if isinstance(seg, dict) and seg.get("fileToken"):
                out.append(seg["fileToken"])
    elif isinstance(v, dict) and v.get("fileToken"):
        out.append(v["fileToken"])
    return out


def get(row: list, idx: int):
    return row[idx] if idx < len(row) else None


def download_media(tokens: TokenManager, file_tokens: list[str], dest: str,
                   prefix: str) -> list[str]:
    """下载 sheets 内嵌图片到 dest，返回本地路径。"""
    paths = []
    for i, ftok in enumerate(file_tokens):
        try:
            u = (f"/drive/v1/medias/batch_get_tmp_download_url"
                 f"?file_tokens={urllib.parse.quote(ftok)}")
            p = _request("GET", u, token=tokens.get())
            urls = p["data"].get("tmp_download_urls") or []
            if not urls:
                continue
            dl = urls[0]["tmp_download_url"]
            local = os.path.join(dest, f"{prefix}_{i+1}.jpg")
            req = urllib.request.Request(dl, headers={"Authorization": f"Bearer {tokens.get()}"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(local, "wb") as fh:
                fh.write(resp.read())
            paths.append(local)
        except Exception as e:  # noqa: BLE001
            log(f"截图下载失败 {ftok[:12]}: {e}")
    return paths


# ── 去重指纹 & state ──────────────────────────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".daily_dispatch_state.json")


def fingerprint(system: str, title: str, desc: str) -> str:
    """系统 | 标题 | 描述前80字SHA1，不绑行号。"""
    h = hashlib.sha1((desc or "")[:80].encode("utf-8")).hexdigest()[:12]
    return f"{system}|{title[:40]}|{h}"


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


def derive_title(desc: str, category: str, menu: str) -> str:
    body = (desc or "").strip()
    if body:
        return body.splitlines()[0].strip()[:60]
    return f"[{menu or '未分类'}] {category or '问题'}"[:60]


# ── 主流程 ─────────────────────────────────────────────────────────────────
def collect(tokens: TokenManager, download: bool, progress_set: set | None = None):
    """拉表 → 筛选 → 综合判定，返回 (items, stats)。items 每条含判定结果与本地截图。
    progress_set：纳入的【解决进展】值集合，默认只 {未解决}（派单口径）；
    后端检查单口径传 {未解决, 处理中}。"""
    progress_set = progress_set or {UNRESOLVED}
    rows = fetch_grid(tokens)
    hdr = rows[0] if rows else []
    stats = {"total_rows": len(rows) - 1, "with_desc": 0, "sys_off": 0,
             "not_unresolved": 0, "matched": 0, "other_systems": {}}
    items = []
    tmpdir = tempfile.mkdtemp(prefix="daily_shots_", dir=os.getcwd()) if download else None

    for ri, row in enumerate(rows[1:], start=2):  # 行号从 2 起（1 是表头）
        desc = cell_text(get(row, COL_DESC))
        if not desc:
            continue
        stats["with_desc"] += 1
        system = cell_text(get(row, COL_SYS)).strip()
        progress = cell_text(get(row, COL_PROGRESS)).strip()

        if system not in TARGET_SYSTEMS:
            stats["sys_off"] += 1
            stats["other_systems"][system or "空"] = stats["other_systems"].get(system or "空", 0) + 1
            continue
        if progress not in progress_set:
            stats["not_unresolved"] += 1
            continue

        stats["matched"] += 1
        menu = cell_text(get(row, COL_MENU)).strip()
        category = cell_text(get(row, COL_CAT)).strip()
        tests = cell_text(get(row, COL_TESTS))
        dev = cell_text(get(row, COL_DEV)).strip()
        priority = cell_text(get(row, COL_PRI)).strip()

        shot_tokens = cell_image_tokens(get(row, COL_SHOT))
        env_tokens = cell_image_tokens(get(row, COL_ENV))
        n_shots = len(shot_tokens)

        cls, difficulty, reason = classify(desc, category, menu, tests, n_shots)
        title = derive_title(desc, category, menu)
        fp = fingerprint(system, title, desc)

        shots_local = []
        if download and (shot_tokens or env_tokens):
            shots_local = download_media(tokens, shot_tokens, tmpdir, f"r{ri}_shot")
            shots_local += download_media(tokens, env_tokens, tmpdir, f"r{ri}_env")

        items.append({
            "row": ri, "system": system, "menu": menu, "category": category,
            "title": title, "desc": desc, "priority": priority, "dev": dev,
            "progress": progress,
            "cls": cls, "difficulty": difficulty, "reason": reason,
            "n_shots": n_shots, "n_env": len(env_tokens),
            "shots_local": shots_local, "fingerprint": fp,
            "repos": repos_for(system, cls),
        })
    stats["tmpdir"] = tmpdir
    return items, stats


def print_report(items: list, stats: dict) -> None:
    fe = [x for x in items if x["cls"] == "frontend"]
    be = [x for x in items if x["cls"] == "backend"]
    un = [x for x in items if x["cls"] == "uncertain"]

    def block(name, arr):
        print(f"\n{'='*70}\n【{name}】 {len(arr)} 条\n{'='*70}")
        for x in arr:
            print(f"  行{x['row']:>3} | {x['system']} | {x['category'] or '-'} | 截图{x['n_shots']} 环境{x['n_env']}")
            print(f"        标题: {x['title']}")
            print(f"        判定依据: {x['reason']}  (难易={x['difficulty']}, 仓库={'+'.join(x['repos'])})")

    print(f"\n数据源: {SHEET_URL}  子表=问题记录({SHEET_ID})")
    print(f"总数据行(有问题描述): {stats['with_desc']}  |  命中(系统∈目标 且 未解决): {stats['matched']}")
    print(f"  排除: 非目标系统 {stats['sys_off']} 条, 非未解决 {stats['not_unresolved']} 条")
    print(f"  非目标系统分布(供陆叙确认 .jp 变体是否纳入): {stats['other_systems']}")
    block("前端清单", fe)
    block("后端清单", be)
    block("不确定清单（不派人）", un)
    print(f"\n{'='*70}\n汇总: 前端 {len(fe)} / 后端 {len(be)} / 不确定 {len(un)}  (命中合计 {len(items)})")
    print(f"截图已下载至: {stats.get('tmpdir') or '(未下载, --no-download)'}\n")


# ── 下拉选项读取 & 回写（L/M 现为单选下拉，必须按选项值写，不能写自由文本）──────
def fetch_list_options(tokens: TokenManager, col: str) -> list[str]:
    """读某列的数据验证(下拉)合法选项值。列非下拉则返回 []。"""
    p = _request("GET",
                 f"/sheets/v2/spreadsheets/{SHEET_TOKEN}/dataValidation"
                 f"?range={SHEET_ID}!{col}1:{col}{MAX_ROW}&dataValidationType=list",
                 token=tokens.get())
    dvs = p["data"].get("dataValidations") or []
    return list(dvs[0].get("conditionValues") or []) if dvs else []


def resolve_ding_write_value(l_options: list[str]) -> str:
    """按偏好挑一个「丁」能落进 L 下拉的合法值；都不在选项里 → 阻塞（不瞎写）。"""
    for v in DING_WRITE_PREFERENCE:
        if v in l_options:
            return v
    raise BlockedError(
        f"L(开发) 下拉里没有可用的「丁」选项（现有={l_options}）。"
        f"请陆叙在飞书把「丁」加进该列选项，或授权改写为「丁志诚」。"
    )


def write_cell(tokens: TokenManager, col: str, row: int, value: str) -> None:
    p = _request("PUT",
                 f"/sheets/v2/spreadsheets/{SHEET_TOKEN}/values",
                 token=tokens.get(),
                 body={"valueRange": {"range": f"{SHEET_ID}!{col}{row}:{col}{row}",
                                      "values": [[value]]}})
    return p


def read_cell(tokens: TokenManager, col: str, row: int) -> str:
    p = _request("GET",
                 f"/sheets/v2/spreadsheets/{SHEET_TOKEN}/values/{SHEET_ID}!{col}{row}:{col}{row}"
                 f"?valueRenderOption=UnformattedValue", token=tokens.get())
    vals = p["data"]["valueRange"]["values"]
    return cell_text(vals[0][0]) if vals and vals[0] else ""


def write_back_row(tokens: TokenManager, row: int, ding_val: str, in_progress: bool,
                   dry: bool) -> None:
    """正式处理单：L=丁选项 + M=处理中；写完抽样回读确认落成选项而非文本。"""
    if dry:
        log(f"[dry] 回写 行{row} L={ding_val}" + (f" M={IN_PROGRESS}" if in_progress else ""))
        return
    write_cell(tokens, "L", row, ding_val)
    if in_progress:
        write_cell(tokens, "M", row, IN_PROGRESS)
    got_l = read_cell(tokens, "L", row)
    if got_l != ding_val:
        raise BlockedError(f"行{row} L 回读={got_l!r} != 期望{ding_val!r}，可能未落成下拉选项，停止。")
    log(f"  ✓ 行{row} L 回读确认={got_l}" + (f"，M→{IN_PROGRESS}" if in_progress else ""))


# ── 建单 ───────────────────────────────────────────────────────────────────
# 正式处理单默认分配项目主管，由其按前后端逐单派人（本任务流程约定）。
AGENT_PM = "9277468f-5521-4ffd-b78f-f95dd7977ece"   # 项目主管docify


def map_priority(feishu_pri: str) -> str:
    return {"高": "high", "中": "medium", "低": "low",
            "P0": "urgent", "P1": "high", "P2": "medium", "P3": "low"}.get(
        (feishu_pri or "").strip(), "none")


# ── 后端检查单（不逐条建单，每天汇总一张）──────────────────────────────────────
# 陆叙口径：后端每天出一张检查单（或发消息），含当下「未解决 + 处理中」的后端问题，
# 每条简单分析好不好改，不给每个后端问题单独建 issue。
LUXU_MEMBER_ID = "5f4f6c9b-7675-44c9-9394-82fee107a79a"   # 陆叙
# 陆叙口径：要「私信/消息、进收件箱、不建单」。Multica 里进人收件箱=@mention 通知，
# 所以后端巡检改为**在本任务下发一条 @陆叙 的评论**（消息，不新建 issue）。
DIGEST_ISSUE_ID = "4ac5bbff-2044-4fae-bb66-d155016b5059"   # MYD-175 本任务


def assess_fixability(x: dict) -> tuple[str, str]:
    """粗评后端问题好不好改，返回 (等级, 一句话依据)。纯启发式，供人工参考。"""
    t = f"{x['desc']}".lower()
    if any(w in t for w in ["无法复现", "偶现", "重现不了", "有时候", "偶尔"]):
        return "难", "无法稳定复现，先要定位触发条件"
    if any(w in t for w in ["新增", "增加功能", "希望支持", "建议", "期望可以"]):
        return "难", "偏新增功能/需求，非单纯修 bug"
    if any(w in t for w in ["性能", "慢", "超时", "卡死", "卡了", "繁忙"]):
        return "中", "性能/超时类，需 profiling 定位瓶颈"
    if any(w in t for w in ["越权", "权限", "鉴权"]):
        return "中", "涉及鉴权/权限逻辑，改动需谨慎回归"
    if any(w in t for w in ["多语言", "i18n", "英文", "语种", "模板"]):
        return "较好", "多为文案/模板 i18n，定位快"
    if any(w in t for w in ["报错", "报了", "error", "500", "502", "validation", "禁止访问", "失败"]):
        return "较好", "有明确报错，可循栈定位"
    if any(w in t for w in ["定时", "cron", "周期"]):
        return "中", "定时/调度逻辑，需覆盖时区/边界"
    return "中", "需看接口日志进一步判断"


def build_backend_digest(items: list, date_str: str) -> str:
    """把后端问题汇总成一张检查单正文（markdown）。含未解决 + 处理中，分组 + 好不好改。"""
    be = [x for x in items if x["cls"] == "backend"]
    unresolved = [x for x in be if x["progress"] == UNRESOLVED]
    inprog = [x for x in be if x["progress"] == IN_PROGRESS]
    lvl_order = {"较好": 0, "中": 1, "难": 2}

    def tbl(arr):
        arr = sorted(arr, key=lambda x: lvl_order.get(assess_fixability(x)[0], 1))
        out = ["| 行 | 系统 | 标题 | 好不好改 | 依据 |", "|---|---|---|---|---|"]
        for x in arr:
            lvl, why = assess_fixability(x)
            t = x["title"].replace("|", "丨")
            out.append(f"| {x['row']} | {x['system']} | {t} | **{lvl}** | {why} |")
        return "\n".join(out)

    from collections import Counter
    lvlc = Counter(assess_fixability(x)[0] for x in be)
    lines = [
        f"> 飞书「问题记录」表后端问题自动巡检 · {date_str} · [打开原表]({SHEET_URL})",
        "",
        f"后端问题共 **{len(be)}** 条（未解决 {len(unresolved)} · 处理中 {len(inprog)}）；"
        f"好不好改：较好 {lvlc['较好']} / 中 {lvlc['中']} / 难 {lvlc['难']}。",
        "",
        "> 说明：本单只做汇总 + 粗评，**不逐条建单、不派人**。「好不好改」为启发式估计，"
        "供排期参考，具体以开发看代码为准。",
        "",
        f"## 未解决（{len(unresolved)}）",
        tbl(unresolved) if unresolved else "（无）",
        "",
        f"## 处理中（{len(inprog)}）",
        tbl(inprog) if inprog else "（无）",
    ]
    return "\n".join(lines)


def send_backend_digest_message(items: list, date_str: str, dry: bool) -> str | None:
    """把后端巡检作为**消息**发进陆叙收件箱：在本任务下发一条 @陆叙 的评论，
    不新建 issue（陆叙口径：要私信/消息、进收件箱、不建单）。返回评论 id。"""
    be = [x for x in items if x["cls"] == "backend"]
    body = (f"[@陆叙](mention://member/{LUXU_MEMBER_ID}) 后端问题每日巡检 · {date_str}\n\n"
            + build_backend_digest(items, date_str))
    if dry:
        log(f"[dry] 发消息给陆叙（评论·不建单）：后端 {len(be)} 条")
        return "dry-run-id"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8",
                                     dir=os.getcwd()) as tf:
        tf.write(body)
        p = tf.name
    # 根评论（不带 --parent）→ 作为新消息进陆叙收件箱。
    cmd = ["multica", "issue", "comment", "add", DIGEST_ISSUE_ID,
           "--content-file", p, "--output", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log(f"后端巡检消息发送失败: {r.stderr.strip()[:300]}")
            return None
        out = r.stdout.strip()
        s = out.find("{")
        return (json.loads(out[s:]) if s >= 0 else {}).get("id")
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


def build_description(x: dict, kind: str) -> str:
    """kind: 'formal'(正式处理单) / 'analysis'(分析问题单)。"""
    repos = "、".join(x["repos"])
    lines = [
        f"> 由飞书「问题记录」表自动建单 · [打开原表]({SHEET_URL}) · 行 {x['row']}",
        "",
        f"- **系统**：{x['system']}",
        f"- **菜单**：{x['menu'] or '-'}　**问题分类**：{x['category'] or '-'}",
        f"- **前后端判定**：{x['cls']}（{x['reason']}）",
        f"- **难易**：{x['difficulty']}　**目标仓库**：{repos}",
        f"- **优先级(原表)**：{x['priority'] or '-'}",
        "",
        "## 问题描述",
        x["desc"] or "（无文字描述，见截图）",
        "",
        BRANCH_HINT,
        f"> 目标仓库：**{repos}**（99% 情况基于 `develop` 起支线）。",
    ]
    # 前端联调 & 提交纪律：前端件必带登录配置提示，按命中的仓库追加对应警告。
    if x["cls"] == "frontend":
        lines += ["", "## 开发提示", DEV_ENV_HINT_COMMON]
        if "docify-web" in x["repos"]:
            lines.append(DEV_ENV_HINT_WEB)
        if "docify-agent" in x["repos"]:
            lines.append(DEV_ENV_HINT_AGENT)
    if x["n_shots"] or x["n_env"]:
        lines += ["", f"## 附件", f"- 问题截图 {x['n_shots']} 张、环境 {x['n_env']} 张（见附件区）"]
    if kind == "analysis":
        lines += ["", "> ⚠️ 归属不确定/较难，作为**分析问题单**：先分析定位，不预先派人，等授权后再转正式处理单。"]
    return "\n".join(lines)


def create_issue(x: dict, kind: str, dry: bool) -> str | None:
    """建 Multica issue，返回 issue_id。正式处理单分配项目主管；分析单不派人。"""
    title = ("[前端]" if x["cls"] == "frontend" else "[后端]" if x["cls"] == "backend" else "[待定]") \
        + x["title"]
    title = title[:60]
    priority = map_priority(x["priority"])
    if dry:
        log(f"[dry] 建单《{title}》 kind={kind} 分配={'项目主管' if kind=='formal' else '不派人'} "
            f"附件={len(x['shots_local'])}")
        return "dry-run-id"
    desc = build_description(x, kind)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8",
                                     dir=os.getcwd()) as tf:
        tf.write(desc)
        desc_path = tf.name
    cmd = ["multica", "issue", "create", "--title", title,
           "--description-file", desc_path, "--project", PROJECT_ID,
           "--status", "todo", "--allow-duplicate", "--output", "json"]
    if kind == "formal":
        cmd += ["--assignee-id", AGENT_PM]
    if priority != "none":
        cmd += ["--priority", priority]
    for a in x["shots_local"]:
        cmd += ["--attachment", a]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log(f"建单失败《{title}》: {r.stderr.strip()[:300]}")
            return None
        out = r.stdout.strip()
        start = out.find("{")
        issue = json.loads(out[start:]) if start >= 0 else {}
        return issue.get("id") or issue.get("identifier")
    finally:
        try:
            os.unlink(desc_path)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="飞书「问题记录」→ Multica issue 每日分派")
    ap.add_argument("--report", action="store_true",
                    help="只读，产出前端/后端/不确定三张清单 + 命中统计（默认下载截图）")
    ap.add_argument("--dry-run", action="store_true",
                    help="全链路自检（筛选/系统→仓库/难易/前后端 + 下拉选项可写性），不建单不写回")
    ap.add_argument("--no-download", action="store_true", help="跳过截图下载（更快）")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON 结果到 stdout")
    ap.add_argument("--execute", action="store_true",
                    help="真实建单 + 回写（需 L「丁」选项就绪；否则阻塞报错）。慎用。")
    ap.add_argument("--limit", type=int, default=0,
                    help="配合 --execute：只处理前 N 条做小样验证（0=全量）")
    ap.add_argument("--backend-digest", action="store_true",
                    help="建一张后端问题检查单（未解决+处理中，含好不好改粗评，@陆叙），不逐条建单。"
                         "配合 --execute 才真实建单，否则 dry 预览。")
    ap.add_argument("--date", default="", help="检查单日期(YYYY-MM-DD)，默认取本机当天")
    args = ap.parse_args()

    app_id, app_secret = os.environ.get("FEISHU_APP_ID"), os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        log("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return 1

    tokens = TokenManager(app_id, app_secret)

    # 下拉选项可写性预检（三种模式都先查，报告里要用）
    l_options = fetch_list_options(tokens, "L")
    m_options = fetch_list_options(tokens, "M")
    log(f"L(开发) 下拉选项={l_options}")
    log(f"M(解决进展) 下拉选项={m_options}")
    ding_ready = any(v in l_options for v in DING_WRITE_PREFERENCE)

    # ── 后端检查单模式（不逐条建单，汇总一张）──
    if args.backend_digest:
        date_str = args.date or time.strftime("%Y-%m-%d")
        # 后端口径更宽：未解决 + 处理中。截图对汇总表无用，不下载。
        items, stats = collect(tokens, download=False,
                               progress_set={UNRESOLVED, IN_PROGRESS})
        be = [x for x in items if x["cls"] == "backend"]
        log(f"后端检查单 {date_str}：后端问题 {len(be)} 条"
            f"（未解决 {sum(1 for x in be if x['progress']==UNRESOLVED)} / "
            f"处理中 {sum(1 for x in be if x['progress']==IN_PROGRESS)}）")
        mid = send_backend_digest_message(items, date_str, dry=not args.execute)
        log(f"{'完成' if args.execute else 'DRY'}：后端巡检消息(评论·不建单) id={mid}")
        return 0

    read_only = args.report or args.dry_run or not args.execute
    if not args.execute:
        if not (args.report or args.dry_run):
            log("⚠️ 未指定模式。只读用 --report/--dry-run；真实建单回写用 --execute（慎用）。")
            return 2
        log(f"读取电子表格 {SHEET_TOKEN} 子表 {SHEET_ID}（只读）")
        items, stats = collect(tokens, download=not args.no_download)
        print_report(items, stats)
        if not ding_ready:
            log("⚠️ 阻塞预警：L 下拉无「丁」选项，正式处理单无法回写 L，真实建单需等陆叙决策。")
        if args.json:
            slim = [{k: v for k, v in x.items() if k != "shots_local"} | {"n_shots_local": len(x["shots_local"])}
                    for x in items]
            print("\n===JSON===")
            print(json.dumps({"stats": {k: v for k, v in stats.items() if k != "tmpdir"},
                              "l_options": l_options, "m_options": m_options,
                              "items": slim}, ensure_ascii=False, indent=2))
        log(f"完成（只读）：命中 {stats['matched']} 条。")
        return 0

    # ── --execute 真实模式 ──
    try:
        ding_val = resolve_ding_write_value(l_options)   # 选项不就绪直接抛 BlockedError
    except BlockedError as e:
        log(f"❌ 阻塞，未做任何写入：{e}")
        return 3
    log(f"真实模式：L 将写选项「{ding_val}」" + (f"（仅前 {args.limit} 条小样）" if args.limit else "（全量）"))
    items, stats = collect(tokens, download=not args.no_download)
    # 只有「简单件」才走正式处理单（派人 + 回写 L/M）；难/不确定不动，等授权。
    easy = [x for x in items if x["cls"] != "uncertain" and x["difficulty"] == "easy"]
    if args.limit:
        easy = easy[:args.limit]
    log(f"本次真实处理 {len(easy)} 条简单件（正式处理单）；其余进分析单/不确定，不派人不回写。")
    state = load_state()
    done = 0
    for x in easy:
        fp = x["fingerprint"]
        if fp in state:
            # 已存在 issue → 重置为 todo 交陆叙人工，不重复建单（幂等）。
            log(f"  跳过(已建过) 行{x['row']} → {state[fp]}")
            continue
        issue_id = create_issue(x, "formal", dry=False)
        if not issue_id:
            log(f"  建单失败，跳过回写：行{x['row']}")
            continue
        state[fp] = issue_id
        save_state(state)   # 建单成功即落 state，回写失败也不会重复建单
        try:
            write_back_row(tokens, x["row"], ding_val, in_progress=True, dry=False)
        except BlockedError as e:
            log(f"❌ 行{x['row']} 回写阻塞（issue={issue_id} 已建），停止：{e}")
            return 3
        done += 1
        log(f"  ✓ 行{x['row']} issue={issue_id} 已派项目主管，L={ding_val}/M={IN_PROGRESS}")
        time.sleep(0.3)
    log(f"完成（真实）：处理 {done}/{len(easy)} 条简单件（正式处理单）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
