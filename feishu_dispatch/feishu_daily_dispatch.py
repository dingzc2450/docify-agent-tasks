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

MYD-272 定稿的建单规则（2026-09-07 并入本脚本，替换旧的本地 state 去重）：
  1. **查重是双键、且查平台不查本地**。行号做主键已被证伪（行 158 五天内指向两条
     不同记录，原表上方插行导致漂移），本地 state 又会被 autopilot 每次 checkout 抹掉。
     改为把键写进 issue metadata，用 `multica issue list --metadata k=v` 精确查：
       · feishu_fingerprint_v2 = sha1(系统|菜单|问题分类|问题描述|更新日期|截图fileToken) —— 主键
       · feishu_fingerprint    = sha1(系统|菜单|问题分类|问题描述|更新日期)             —— 次级键/兜底
       · feishu_row            = <表token>!<sheetId>#<行号> —— 仅提示，会漂移
       · feishu_row_verified   = bool，表示行号在建单当刻核对过
     v2 优先，未命中再查 v1（截图被重传会换 token，v2 会假阴性，故 v1 必须保留）。
     无截图的行 v2 不可算（原表约 19 行），**自动降级为只用 v1**。
  2. **v1 相撞的行禁用 v1 兜底**。全表跑一遍 v1，凡指纹重复的行（实测「会话区」5 行
     同系统同菜单同分类且描述全空）只认 v2；这些行若又没截图 → 不建单，进人工清单。
  3. **跳过必须留痕**。每轮扫描都要打印跳过了哪些行、命中的是哪个键、撞上的是哪张单，
     不允许静默跳过——否则漏建和重复建都查不出来。
  4. **优先级按口径机械判定**：功能问题=medium、纯 UI/文案=low；整页不可用或数据取不到
     上调一档；dev/测试环境的行封顶 medium（不出 high），生产域名的行才可能到 high。
  5. 截图落地按 magic number 定扩展名（原表 PNG/JPEG 混用，文件名不可信）。

MYD-272 复核后的第二轮修正（项目主管 2026-09-07 逐段看代码提的）：
  6. **建单不派人、不回写飞书**。旧行为是每建一张单就 assign 项目主管，等于每建一张
     就点起他一个 run，与「全部落地后集中分诊一轮」冲突；而建单即把原表 M 写成
     「处理中」更是往测试在看的信息源里写了个不准的状态——那一刻根本没人开工。
     现在：建单只建单，L/M 回写跟着**真实指派**走（--writeback-on-create 可恢复旧行为）。
     代价是少了「M=处理中 自动排除」这层幂等，所以查重必须是硬的 → 见第 7 条。
  7. **metadata 写不进去就当这轮失败**。查重键是建单之后单独写的，中间断掉那张单就
     对前两层查重隐形（历史上 59 张单就是这个下场）。现在写完立刻回读校验，失败则重试、
     再失败就落进 .metadata_backfill_needed.json 并**中止本轮**。注意那个文件是
     会话级的（随 checkout 消失），只当人工在场时的提前报警用 → 真正的兜底见第 10 条。
  8. **环境列为空按 dev 处理**（封顶 medium）。整轮都是 dev 阶段的测试发现，环境未知时
     按生产放行会让「白屏」「崩溃」这类词直接判 high。
  9. 前提（实测验证过，别改成「只查未关闭的单」）：`multica issue list --metadata`
     **能查到已关闭的单**（done/cancelled 都精确返回）。整套查重的成立全靠这一点——
     16 张单做完转 done 之后，下一轮扫表必须仍能查到它们，否则会全部重建一遍。

MYD-272 第三轮修正（项目主管 2026-09-07 指出闸门放错了地方）：
 10. **查重加第三层：描述溯源块兜底**。第 7 条的失败清单放在本地目录，而本地文件
     会被 autopilot 每次 checkout 抹掉——防止重复建单的最后一道闸门，反而比它守护的
     metadata 更脆弱。闭合的失效路径是：建单成功 → metadata 写失败 → 记本地文件 →
     中止；下轮 checkout 文件没了 → 检查放行 → 两层 metadata 双双 miss → 静默重建。
     根治办法是让**孤儿单自己认领自己**：建单时描述里已经写了 fp1/fp2 溯源块，那段
     文本跟 issue 是**同一个 create 调用原子落地**的，不存在「建了单但键没写上」的
     窗口。所以 metadata 两层都 miss 时，再翻页拉全量 issue、按指纹搜一遍描述；命中
     就顺手把 metadata 补回去。闸门因此退化成锦上添花，不再是唯一防线。
     翻页是硬要求：--limit 默认 50、服务端封顶 100，一页拉不完会静默漏。

环境变量：FEISHU_APP_ID / FEISHU_APP_SECRET

用法：
  python3 feishu_daily_dispatch.py --report            # 只读，产出三类清单 + 命中统计（默认下载截图）
  python3 feishu_daily_dispatch.py --report --no-download   # 只读，跳过截图下载（更快）
  python3 feishu_daily_dispatch.py --dry-run           # 全链路自检（筛选/仓库映射/难易/前后端），不建单不写回
  python3 feishu_daily_dispatch.py                     # 正式执行（复核放行后才用）
"""

import argparse
import collections
import datetime
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
# 行数每周都在涨（2026-08 是 231，2026-09-07 已 330），写死会漏读尾部新行 →
# 运行时查 grid row_count，查不到才退回这个下限。
MAX_ROW_FALLBACK = 600
MAX_ROW = MAX_ROW_FALLBACK

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
                  "Docify.jp官网", "Docify.jp-用户端", "Docify-管理端",
                  "cn官网"}          # MYD-272 新发现的变体：中国站官网，等同 Docify官网
UNRESOLVED = "未解决"
IN_PROGRESS = "处理中"   # M 回写值（M 现为下拉，「处理中」是合法选项，实测确认）
# 【开发】列已标不豁免——仍纳入筛选，靠去重指纹 + 已存在 issue 判定收敛。
# L(开发) 列 2026-08-12 起由自由文本改为**单选下拉**，合法选项实测=
#   {丁志诚, 者俊, 闫超, 陈浩}——注意没有裸「丁」！旧文本「丁」「丁？」是遗留脏值。
# 「分配给丁」的判定 = L ∈ 下面这些值（含遗留文本 + 正式选项）。
DING_VALUES = {"丁", "丁？", "丁志诚"}
# 已经写了别人名字的行**不抢**（MYD-272 口径：那轮的筛选是 系统∈目标 AND 未解决
# AND 开发=丁志诚）。实测 L 列里 闫超 107 行、者俊 11、陈浩 3、冯志康 2——如果放任
# 「简单前端件」那条老规则去建单，会把别人名下的 bug 又派给丁一遍。
OTHER_OWNERS = {"者俊", "闫超", "陈浩", "冯志康"}
# 回写 L 时的目标：优先裸「丁」（若被加进选项），否则用已存在的合法选项「丁志诚」。
# 二者都不在选项里 → 阻塞，不瞎写文本（会破坏下拉校验）。
DING_WRITE_PREFERENCE = ["丁", "丁志诚"]


class BlockedError(RuntimeError):
    """遇到需要陆叙决策的阻塞（如选项不存在），不硬写。"""

# ── 系统 → 仓库 ────────────────────────────────────────────────────────────
def repos_for(system: str, category: str) -> list[str]:
    """系统 + 前后端归属 → 目标仓库。"""
    if system in ("Docify官网", "Docify.jp官网", "cn官网"):
        return ["docify-main"]                   # 官网(含 jp / cn 变体)，前端为主
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
def probe_max_row(tokens: TokenManager) -> int:
    """查子表真实 grid row_count；失败退回 MAX_ROW_FALLBACK。
    写死行数会漏读尾部新增行（这张表每周都在长），所以每次运行都探一次。"""
    global MAX_ROW
    try:
        p = _request("GET", f"/sheets/v3/spreadsheets/{SHEET_TOKEN}/sheets/query",
                     token=tokens.get())
        for s in p["data"].get("sheets") or []:
            if s.get("sheet_id") == SHEET_ID:
                n = int((s.get("grid_properties") or {}).get("row_count") or 0)
                if n > 0:
                    MAX_ROW = n
                    log(f"子表 {SHEET_ID} 实际行数={n}")
                    return n
    except Exception as e:  # noqa: BLE001
        log(f"行数探查失败，退回 {MAX_ROW_FALLBACK}: {e}")
    MAX_ROW = MAX_ROW_FALLBACK
    return MAX_ROW


def fetch_grid(tokens: TokenManager) -> list[list]:
    """拉整块 A1:N{MAX_ROW}，UnformattedValue 渲染——文本正常返回，
    内嵌图片(embed-image)单元格会带 fileToken，可下载。
    ⚠️ 不能换成 ToString/FormattedValue：那两种渲染会把 fileToken 抹掉，v2 指纹就算不出来。"""
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


# ── 指纹专用取值（MYD-272）─────────────────────────────────────────────────
# 与 cell_text 刻意分开：cell_text 有 link/name 兜底、会随渲染改动，
# 而指纹一旦变了就等于全表重建单。这两个函数是**哈希输入的一部分，不要改**。
def fp_text(v) -> str:
    """指纹口径的单元格文本：只取 text 段，图片段计空串。"""
    if v is None:
        return ""
    if isinstance(v, list):
        return "".join((s.get("text") or "") if isinstance(s, dict) else str(s)
                       for s in v).strip()
    if isinstance(v, dict):
        return (v.get("text") or "").strip()
    return str(v).strip()


def fp_date(v) -> str:
    """K【更新日期】在 UnformattedValue 下是 Excel 序列号 → 归一成 YYYY/MM/DD。"""
    raw = fp_text(v)
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return raw
    return (datetime.date(1899, 12, 30) + datetime.timedelta(days=n)).strftime("%Y/%m/%d")


IMAGE_MAGIC = [(b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
               (b"GIF8", ".gif"), (b"RIFF", ".webp"), (b"BM", ".bmp")]


def image_ext(head: bytes) -> str | None:
    """按 magic number 定扩展名。原表内嵌图 PNG/JPEG 混用且文件名不可信，
    扩展名写错 Multica 侧就渲染不出缩略图。识别不出 → None（不是图片，多半是错误 JSON）。"""
    for sig, ext in IMAGE_MAGIC:
        if head.startswith(sig):
            return ext
    return None


def download_media(tokens: TokenManager, file_tokens: list[str], dest: str,
                   prefix: str) -> list[str]:
    """下载 sheets 内嵌图片到 dest，返回本地路径。
    两条注意（MYD-272 踩过）：
      · Authorization 头必须真的带上 token，否则接口返 400 并把 JSON 错误写进目标文件，
        得到一个「看起来下载成功」的坏图 → 这里按 magic number 校验，非图片直接丢弃。
      · 扩展名按内容判定，不信文件名（同一列里 PNG/JPEG 混用）。"""
    paths = []
    for i, ftok in enumerate(file_tokens):
        try:
            u = (f"/drive/v1/medias/batch_get_tmp_download_url"
                 f"?file_tokens={urllib.parse.quote(ftok)}")
            p = _request("GET", u, token=tokens.get())
            urls = p["data"].get("tmp_download_urls") or []
            if not urls:
                log(f"截图无下载地址 {ftok[:12]}")
                continue
            dl = urls[0]["tmp_download_url"]
            req = urllib.request.Request(dl, headers={"Authorization": f"Bearer {tokens.get()}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                blob = resp.read()
            ext = image_ext(blob[:16])
            if not ext:
                log(f"截图内容不是图片(可能是鉴权失败的 JSON)，丢弃 {ftok[:12]}: {blob[:80]!r}")
                continue
            local = os.path.join(dest, f"{prefix}_{i+1}{ext}")
            with open(local, "wb") as fh:
                fh.write(blob)
            paths.append(local)
        except Exception as e:  # noqa: BLE001
            log(f"截图下载失败 {ftok[:12]}: {e}")
    return paths


# ── 去重指纹（MYD-272 定稿：v2 主键 + v1 兜底，写进 issue metadata）──────────
# 三个 metadata 键名是**跨轮次契约**，改名等于全表重建单，不要动。
MK_FP1 = "feishu_fingerprint"        # sha1(系统|菜单|问题分类|问题描述|更新日期)
MK_FP2 = "feishu_fingerprint_v2"     # 上式再拼 |截图fileToken 列表 —— 主键
MK_ROW = "feishu_row"                # <表token>!<sheetId>#<行号>，仅提示，会漂移
MK_ROW_OK = "feishu_row_verified"    # bool，建单当刻核对过行号

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".daily_dispatch_state.json")
METADATA_FAIL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  ".metadata_backfill_needed.json")
# 注意：这个文件在 autopilot 每次重新 checkout 本仓时会被抹掉，和 STATE_FILE
# 一样是会话级的。曾经它是防止重复建单的最后一道闸门，但现在 find_existing 的
# 第三层（描述溯源块）提供了跨轮次可靠的兜底——指纹串跟 issue 在同一个 create
# 调用里原子落地，不需要单独的文件来记载。所以这个文件现在是**锦上添花**：
# 人工在场时能提前报警，无人值守时闸门失效了也不至于重复建单（会被第三层认领）。


def fingerprint_v1(system: str, menu: str, category: str, desc: str, date: str) -> str:
    """内容指纹，不含行号（行号会因上方插行漂移，行 158 已实证）。"""
    return hashlib.sha1("|".join([system, menu, category, desc, date]).encode()).hexdigest()


def fingerprint_v2(system: str, menu: str, category: str, desc: str, date: str,
                   shot_tokens: list[str]) -> str | None:
    """v1 再拼截图 fileToken —— 实测全表唯一，作主键。
    无截图 → None（约 19 行），这类行自动降级为只用 v1 查重。"""
    if not shot_tokens:
        return None
    return hashlib.sha1(
        "|".join([system, menu, category, desc, date, ",".join(shot_tokens)]).encode()
    ).hexdigest()


def row_key(row: int) -> str:
    return f"{SHEET_TOKEN}!{SHEET_ID}#{row}"


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


# ── 平台侧查重（本地 state 会被 autopilot 每次 checkout 抹掉，不能当真相）────────
_dedup_cache: dict[str, list] = {}


def query_issues_by_metadata(key: str, value: str) -> list[dict]:
    """multica issue list --metadata k=v，精确查已建单。查不动 → 抛异常（宁可停也不重复建单）。

    **前提（项目主管 2026-09-07 实测确认）：--metadata 查询能查到已关闭的单**，
    done / cancelled 都会精确返回（拿 MYD-165 done、MYD-11 cancelled 验证过）。
    整套查重都压在这条上面——如果哪天平台改成默认只查开放单，所有已修完的历史问题
    会瞬间变成「没建过」被重复建一遍。改动查询参数前先重验这一条。
    """
    ck = f"{key}={value}"
    if ck in _dedup_cache:
        return _dedup_cache[ck]
    cmd = ["multica", "issue", "list", "--metadata", f"{key}={value}",
           "--limit", "20", "--output", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"查重失败 {ck}: {r.stderr.strip()[:200]}")
    out = r.stdout.strip()
    s = out.find("{")
    issues = (json.loads(out[s:]) if s >= 0 else {}).get("issues") or []
    _dedup_cache[ck] = issues
    return issues


_desc_index: list[dict] | None = None


def load_issue_descriptions() -> list[dict]:
    """把全工作区 issue 的描述拉一遍（翻页拉全），每轮只拉一次、缓存复用。

    这是**第三层兜底专用**的全量索引，正常查重仍走 metadata 精确查（`--metadata k=v`），
    不要拿它替代前两层——那样每轮都要拖全量数据，且查重精度反而更差。
    翻页是必须的：`--limit` 默认 50、服务端封顶 100，一页拉不完就会静默漏。
    """
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
        s = r.stdout.find("{")
        page = json.loads(r.stdout[s:]) if s >= 0 else {}
        batch = page.get("issues") or []
        issues += batch
        if not page.get("has_more") or not batch:
            break
        offset += len(batch)
        if offset > 20000:      # 防跑飞：真到这个量级说明分页参数出问题了
            raise RuntimeError("issue 全量索引翻页超过 20000 条，疑似分页失效，停止")
    _desc_index = issues
    log(f"  已建单描述索引：{len(issues)} 张（第三层兜底用，本轮只拉一次）")
    return _desc_index


def find_in_descriptions(key: str, fp: str) -> list[dict]:
    """在 issue 描述正文的「自动建单溯源」块里找指纹。

    匹配的是 build_description 渲染出来的整行（`键`：`指纹`），不是裸 sha1 子串——
    MYD-272/273 这类讨论单的正文里会原样引用指纹，裸搜会把讨论单当成问题单命中，
    然后**静默漏建**一张真问题单。多一个键名前缀就能把讨论单排除掉。
    """
    if not fp:
        return []
    needle = f"`{key}`：`{fp}`"
    return [i for i in load_issue_descriptions() if needle in (i.get("description") or "")]


def find_existing(x: dict) -> tuple[dict | None, str]:
    """按 v2 → v1 → 描述溯源块 三层查已建单。返回 (issue|None, 命中的键名/未命中原因)。

    规则来源 MYD-272：
      · v2 优先：含截图 fileToken，实测全表唯一。
      · v1 兜底：截图被重传会换 token → v2 假阴性，必须再查一次 v1。
      · v1 相撞的行（同系统/菜单/分类且描述为空的那几行）**禁用 v1 兜底**，
        否则会把邻行的单当成自己的单而漏建。

    第三层「描述溯源块」是**孤儿单的自救通道**（项目主管 2026-09-07 提的）：
    metadata 是建单之后另一次 API 调用写的，中间断掉就会留下一张有单无键的孤儿单，
    对前两层完全隐形、下轮必被重复建。而描述里的指纹串是**跟 issue 在同一个
    create 调用里原子落地的**——不存在「建了单但键没写上」的窗口，所以两层 metadata
    都 miss 时再按指纹搜一遍描述，孤儿单就能自己认领自己。命中后顺手把 metadata
    补回去（补不上也不致命，下轮这层还会再兜住它）。
    """
    if x["fp2"]:
        hit = query_issues_by_metadata(MK_FP2, x["fp2"])
        if hit:
            return hit[0], MK_FP2
    if not x["v1_ambiguous"]:
        hit = query_issues_by_metadata(MK_FP1, x["fp1"])
        if hit:
            return hit[0], MK_FP1
    # ── 第三层：描述正文里的溯源块。v2 精确，v1 沿用同一条相撞禁用规则。
    for key, fp, why in ((MK_FP2, x["fp2"], "描述溯源块(v2)"),
                         (MK_FP1, None if x["v1_ambiguous"] else x["fp1"], "描述溯源块(v1)")):
        found = find_in_descriptions(key, fp)
        if found:
            iss = found[0]
            if len(found) > 1:
                log(f"  ⚠️ 行{x['row']} 描述兜底命中 {len(found)} 张"
                    f"（{', '.join(i.get('identifier','?') for i in found)}），取第一张")
            log(f"  ↺ 行{x['row']} metadata 查不到但描述里有指纹 → 孤儿单 {iss.get('identifier')}，"
                f"补写 metadata")
            try:
                set_issue_metadata(iss["id"], x, record_failure=False)
            except MetadataWriteError as e:
                log(f"  ⚠️ 孤儿单 {iss.get('identifier')} metadata 补写仍失败：{e}"
                    f"（不致命：下轮描述兜底还会兜住它）")
            return iss, why
    if x["v1_ambiguous"]:
        return None, "未命中(该行v1与其它行相撞，v1兜底已禁用，仅查了v2)"
    return None, "未命中"


def derive_title(desc: str, category: str, menu: str, system: str = "",
                 date: str = "") -> str:
    """标题口径与 MYD-274~289 那 16 张单一致：
      · 有描述 → 多行合成一行，超 60 字截断加省略号；
      · 描述为空（会话区那几行）→ 用「系统 菜单 分类问题（MM/DD，见截图）」，
        因为空描述的行只能靠截图区分，标题里必须带日期否则几张单长得一模一样。"""
    lines = [ln.strip() for ln in (desc or "").splitlines() if ln.strip()]
    if lines:
        t = "，".join(lines)
        return t if len(t) <= 60 else t[:59] + "…"
    md = date[5:].replace("/", "/") if len(date) >= 10 else date
    return f"{system} {menu or '未分类'} {category or ''}问题（{md}，见截图）".strip()[:60]


# ── 优先级口径（MYD-272 定稿，机械判定；对 MYD-274~289 那 16 张单 100% 复现）────
# 原表 J【优先级】填写率低且口径不一（描述全空的行也标「高」），只作参考写进描述，不作准。
# 判定分三步：① 分类定基准档 ② 影响范围上调 ③ dev/测试环境封顶。
FUNC_CATS = {"功能问题", "功能", "性能问题", "性能", "越权问题", "PDF 生成异常",
             "文档生成", "上下文问题", "响应中断", "对话意图理解错误", "对话过程"}
UI_ONLY_CATS = {"UI", "样式", "文案", "多语言", "交互"}
# 分类为空时才用关键词兜底猜基准档（分类填了就以分类为准——232/249/283 都是
# 「功能问题」但描述里带「文案」「翻译」，靠关键词会被误降成 low）。
UI_ONLY_KW = ["文案", "多语言", "未适配", "翻译", "样式", "对齐", "排版",
              "颜色", "主题色", "字体", "图标", "间距", "居中"]
# ① 整页不可用 / 数据取不到 → 任何基准档都上调一档（medium→high）
SEVERE_KW = ["白屏", "打不开", "无法打开", "无法访问", "无法进入", "进不去",
             "无法加载", "加载不出", "加载失败", "崩溃", "闪退", "卡死", "无响应",
             "数据丢失", "数据为空", "查不到数据", "全部消失", "无法登录", "无法保存",
             "无法提交", "无法生成", "500", "502", "404"]
# ② 影响范围是全局/整页（不是单个元素）→ 只把 UI 类的 low 抬到 medium，不推到 high。
#    行 255「导航栏展示错乱」、行 282「工作台UI与设计不符」就是靠这条从 low 变 medium；
#    行 288「页面会撑开变形」是单个元素撑开，不在此列，保持 low。
SCOPE_KW = ["导航栏", "全站", "全局", "整页", "全页", "所有页面", "每个页面",
            "与设计不符", "布局错乱", "展示错乱", "错乱"]
# dev/测试环境 → 封顶 medium（陆叙口径：dev 阶段不出 high）；生产域名才可能到 high
DEV_ENV_KW = ["dev", "test", "测试", "预发", "pre", "staging", "uat", "本地", "sit"]
_TIER = ["low", "medium", "high", "urgent"]


def looks_dev_env(env: str, system: str) -> bool:
    """环境列是否指向 dev/测试。注意：232/233/241 的环境写的是「Docify.jp官网」——
    那是生产域名不是 dev，不能一刀切封顶（MYD-272 里这条被专门纠过）。

    环境列为空按 dev 处理（项目主管 2026-09-07）：这张表大多数行本来就是在测试环境
    填的，空值更可能是「测试环境懒得填」而不是「生产」。判错方向要选代价小的一边——
    误封顶只是少一档、分诊时能提回来；误按生产放行则是把 dev 的问题推成 high。"""
    e = (env or "").strip().lower()
    if not e:
        return True
    if "官网" in env or "docify.jp" in e.replace(" ", ""):
        return False
    return any(k in e for k in DEV_ENV_KW)


def assess_priority(desc: str, category: str, env: str, system: str) -> tuple[str, str]:
    """返回 (priority, 一句话依据)。依据要写进 issue 描述，便于复核和事后追溯。"""
    cat = (category or "").strip()
    text = f"{cat} {desc}".lower()

    # ── ① 基准档
    if not (desc or "").strip():
        # 描述为空的行（会话区那几片）只有截图，rubric 判不了 → 取中档，
        # 并在依据里写明要人工补复现步骤。
        return "medium", "描述为空，无法按口径判定 → 取中档，需向测试补复现步骤"
    if cat in UI_ONLY_CATS:
        base, why = "low", f"纯 UI/文案（分类={cat}）"
    elif cat in FUNC_CATS:
        base, why = "medium", f"功能问题（分类={cat}）"
    elif any(k.lower() in text for k in UI_ONLY_KW):
        base, why = "low", "分类为空，描述偏 UI/文案"
    else:
        base, why = "medium", f"分类={cat or '空'}，按功能问题取中档"

    # ── ② 上调
    sev = [k for k in SEVERE_KW if k.lower() in text]
    if sev:
        base = _TIER[min(_TIER.index(base) + 1, len(_TIER) - 1)]
        why += f"；整页不可用/数据取不到上调一档（命中「{sev[0]}」）"
    elif base == "low":
        scope = [k for k in SCOPE_KW if k.lower() in text]
        if scope:
            base = "medium"
            why += f"；影响范围为全局/整页上调一档（命中「{scope[0]}」）"

    # ── ③ 封顶
    if base in ("high", "urgent") and looks_dev_env(env, system):
        base, why = "medium", why + f"；环境={env} 属 dev/测试，封顶 medium"
    return base, why


# ── 主流程 ─────────────────────────────────────────────────────────────────
def scan_v1_collisions(rows: list[list]) -> tuple[dict[int, str], set[str]]:
    """全表跑一遍 v1 指纹，找出相撞的指纹（MYD-272：实测「会话区」5 行相撞）。

    必须在**全表**上算，不能只在命中行上算——邻行可能因为进展/系统不同没进候选，
    但它已经建过单了，v1 兜底照样会撞上去。返回 (行号→v1, 相撞的 v1 集合)。
    """
    per_row: dict[int, str] = {}
    for ri, row in enumerate(rows[1:], start=2):
        f = [fp_text(get(row, COL_SYS)), fp_text(get(row, COL_MENU)),
             fp_text(get(row, COL_CAT)), fp_text(get(row, COL_DESC))]
        if not any(f):          # 整行空，跳过
            continue
        per_row[ri] = fingerprint_v1(*f, fp_date(get(row, COL_DATE)))
    counts = collections.Counter(per_row.values())
    return per_row, {fp for fp, n in counts.items() if n > 1}


def collect(tokens: TokenManager, download: bool, progress_set: set | None = None):
    """拉表 → 筛选 → 综合判定，返回 (items, stats)。items 每条含判定结果与本地截图。
    progress_set：纳入的【解决进展】值集合，默认只 {未解决}（派单口径）；
    后端检查单口径传 {未解决, 处理中}。"""
    progress_set = progress_set or {UNRESOLVED}
    rows = fetch_grid(tokens)
    v1_by_row, v1_collided = scan_v1_collisions(rows)
    if v1_collided:
        bad = sorted(r for r, f in v1_by_row.items() if f in v1_collided)
        log(f"v1 指纹相撞的行（这些行禁用 v1 兜底，只认 v2）：{bad}")
    stats = {"total_rows": len(rows) - 1, "with_desc": 0, "sys_off": 0,
             "not_unresolved": 0, "matched": 0, "other_systems": {},
             "v1_collided_rows": sorted(r for r, f in v1_by_row.items() if f in v1_collided),
             "no_shot_rows": []}
    items = []
    tmpdir = tempfile.mkdtemp(prefix="daily_shots_", dir=os.getcwd()) if download else None

    for ri, row in enumerate(rows[1:], start=2):  # 行号从 2 起（1 是表头）
        # 指纹口径的取值（fp_*），与展示口径 cell_text 分开，见 fp_text 注释。
        f_sys, f_menu = fp_text(get(row, COL_SYS)), fp_text(get(row, COL_MENU))
        f_cat, f_desc = fp_text(get(row, COL_CAT)), fp_text(get(row, COL_DESC))
        f_date = fp_date(get(row, COL_DATE))

        system = f_sys
        progress = cell_text(get(row, COL_PROGRESS)).strip()
        shot_tokens = cell_image_tokens(get(row, COL_SHOT))

        # 描述为空但有截图的行（会话区那几行）不能丢——它们是真问题，只是描述没填。
        if not f_desc and not shot_tokens:
            continue
        stats["with_desc"] += 1

        if system not in TARGET_SYSTEMS:
            stats["sys_off"] += 1
            stats["other_systems"][system or "空"] = stats["other_systems"].get(system or "空", 0) + 1
            continue
        if progress not in progress_set:
            stats["not_unresolved"] += 1
            continue

        stats["matched"] += 1
        menu, category, desc = f_menu, f_cat, f_desc
        tests = cell_text(get(row, COL_TESTS))
        env = cell_text(get(row, COL_ENV)).strip()
        dev = cell_text(get(row, COL_DEV)).strip()
        priority_raw = cell_text(get(row, COL_PRI)).strip()

        env_tokens = cell_image_tokens(get(row, COL_ENV))
        n_shots = len(shot_tokens)
        if not shot_tokens:
            stats["no_shot_rows"].append(ri)

        cls, difficulty, reason = classify(desc, category, menu, tests, n_shots)
        title = derive_title(desc, category, menu, system, f_date)
        fp1 = v1_by_row.get(ri) or fingerprint_v1(f_sys, f_menu, f_cat, f_desc, f_date)
        fp2 = fingerprint_v2(f_sys, f_menu, f_cat, f_desc, f_date, shot_tokens)
        v1_ambiguous = fp1 in v1_collided
        priority, pri_why = assess_priority(desc, category, env, system)

        shots_local = []
        if download and (shot_tokens or env_tokens):
            shots_local = download_media(tokens, shot_tokens, tmpdir, f"r{ri}_shot")
            shots_local += download_media(tokens, env_tokens, tmpdir, f"r{ri}_env")

        items.append({
            "row": ri, "row_key": row_key(ri),
            "system": system, "menu": menu, "category": category,
            "title": title, "desc": desc, "date": f_date, "env": env,
            "priority_raw": priority_raw, "priority": priority, "pri_why": pri_why,
            "dev": dev, "progress": progress,
            "cls": cls, "difficulty": difficulty, "reason": reason,
            "n_shots": n_shots, "n_env": len(env_tokens),
            "shots_local": shots_local,
            "fp1": fp1, "fp2": fp2, "v1_ambiguous": v1_ambiguous,
            # 既无截图又 v1 相撞 → 两个键都不能唯一定位，不能自动建单。
            "undedupable": v1_ambiguous and not fp2,
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
            print(f"        优先级: {x['priority']}（{x['pri_why']}；原表={x['priority_raw'] or '空'}）")
            print(f"        查重键: v2={(x['fp2'] or '—')[:16]} v1={x['fp1'][:16]}"
                  + ("  ⚠️v1兜底禁用(相撞)" if x['v1_ambiguous'] else "")
                  + ("  ⚠️无截图,仅v1" if not x['fp2'] else ""))

    print(f"\n数据源: {SHEET_URL}  子表=问题记录({SHEET_ID})  已读到第 {MAX_ROW} 行")
    print(f"总数据行(有描述或有截图): {stats['with_desc']}  |  命中(系统∈目标 且 进展∈口径): {stats['matched']}")
    print(f"  排除: 非目标系统 {stats['sys_off']} 条, 进展不符 {stats['not_unresolved']} 条")
    print(f"  非目标系统分布: {stats['other_systems']}")
    print(f"  v1 指纹相撞的行(禁用 v1 兜底): {stats.get('v1_collided_rows') or '无'}")
    print(f"  命中行里无截图(v2 不可算, 降级只用 v1): {stats.get('no_shot_rows') or '无'}")
    block("前端清单", fe)
    block("后端清单", be)
    block("不确定清单（不派人）", un)
    print(f"\n{'='*70}\n汇总: 前端 {len(fe)} / 后端 {len(be)} / 不确定 {len(un)}  (命中合计 {len(items)})")
    print(f"截图已下载至: {stats.get('tmpdir') or '(未下载, --no-download)'}\n")


def print_skips(skips: list[dict]) -> None:
    """跳过留痕（MYD-272 硬性要求）：跳过了哪些行、命中哪个键、撞上哪张单，
    每轮都要打全。静默跳过会让「漏建」和「重复建」都变成无法追查的问题。"""
    print(f"\n{'='*70}\n【本轮跳过明细】 {len(skips)} 行\n{'='*70}")
    if not skips:
        print("  （无跳过）")
        return
    for s in skips:
        line = f"  行{s['row']:>3} | {s['reason']}"
        if s.get("key"):
            line += f" | 命中键={s['key']}"
        if s.get("issue"):
            line += f" | 已存在={s['issue']}"
        print(line)
        print(f"        标题: {s.get('title', '')}")


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
# 建单一律不派人：指派会立刻点起对方的 run（项目主管 2026-09-07）。留着这个 id
# 只是为了分诊时人工引用，脚本不再自动写 --assignee-id。
AGENT_PM = "9277468f-5521-4ffd-b78f-f95dd7977ece"   # 项目主管docify（供人工分诊引用）


def map_priority(feishu_pri: str) -> str:
    """保留旧版本外壳（兼容 import 等），但 MYD-272 后 ——execute 不再用这个函数，
    全走 assess_priority() 机械判定。"""
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
    """kind: 'formal'(正式处理单) / 'analysis'(分析问题单)。
    MYD-272 强化：描述末尾加查重键溯源块（与 metadata 双写），描述为空的行明写
    「需向测试补充复现步骤」——那几行只有截图，开发拿到单必须知道要回头问测试。"""
    repos = "、".join(x["repos"])
    desc_body = x["desc"] or "_原表此行描述为空，需向测试补充复现步骤。请以附件截图为准。_"
    lines = [
        f"> 由飞书「问题记录」表自动建单 · [打开原表]({SHEET_URL}) · 行 {x['row']}",
        "",
        f"- **系统**：{x['system']}",
        f"- **菜单**：{x['menu'] or '-'}　**问题分类**：{x['category'] or '-'}",
        f"- **前后端判定**：{x['cls']}（{x['reason']}）",
        f"- **难易**：{x['difficulty']}　**目标仓库**：{repos}",
        f"- **优先级**：{x['priority']}　（{x['pri_why']}；原表={x['priority_raw'] or '空'}）",
        "",
        "## 问题描述",
        desc_body,
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
    # 溯源块：与 metadata 双写。metadata 用来精确查重，这段文本用来人工核对/历史比对。
    lines += [
        "",
        "---",
        "<!-- 自动建单溯源，请勿手改 -->",
        f"- 查重主键 `{MK_FP2}`：`{x['fp2'] or '（该行无截图，v2 不可算，仅用 v1）'}`",
        f"- 次级键 `{MK_FP1}`：`{x['fp1']}`",
        f"- 行号（提示字段，可能因插行漂移）：`{x['row_key']}`",
    ]
    return "\n".join(lines)


class MetadataWriteError(RuntimeError):
    """查重键没能写进 issue metadata —— 那张单从此对查重隐形，必须中止本轮。"""


def _metadata_kv(x: dict) -> list[tuple[str, str, str]]:
    kv = [(MK_FP1, x["fp1"], "string"),
          (MK_ROW, x["row_key"], "string"),
          (MK_ROW_OK, "true", "bool")]
    if x["fp2"]:
        kv.insert(0, (MK_FP2, x["fp2"], "string"))
    return kv


def record_metadata_failure(issue_id: str, x: dict, err: str) -> None:
    """把写失败的单落盘，供人工在场时提前报警。

    **不要把它当跨轮次的闸门**：文件在 autopilot 下一次 checkout 时就没了，
    见 METADATA_FAIL_FILE 处的说明。真正跨轮次兜住孤儿单的是 find_existing 的
    第三层描述兜底。
    """
    try:
        pend = json.load(open(METADATA_FAIL_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        pend = []
    pend.append({"issue": issue_id, "row": x["row"], "title": x["title"],
                 "keys": {k: v for k, v, _ in _metadata_kv(x)},
                 "error": err, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    try:
        with open(METADATA_FAIL_FILE, "w", encoding="utf-8") as fh:
            json.dump(pend, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"❌ 连失败清单都写不下去了（{e}），请立刻人工给 {issue_id} 补 metadata")


def check_pending_metadata_failures() -> list:
    try:
        return json.load(open(METADATA_FAIL_FILE, encoding="utf-8")) or []
    except (OSError, ValueError):
        return []


def set_issue_metadata(issue_id: str, x: dict, retries: int = 3,
                       record_failure: bool = True) -> None:
    """把查重键写进 issue metadata，写完**回读校验**。

    这一步不是「尽力而为」：本地 state 会被 autopilot 每次 checkout 抹掉，建单又
    不再回写 M=处理中，metadata 是跨轮次最主要的幂等来源。写漏一张 = 那张单对前两层
    查重隐形（历史上 59 张单正是这个下场，靠 MYD-273 全量回填才补回来），只能靠
    find_existing 第三层的描述兜底捞回来。所以失败要重试、重试完还不行就抛
    MetadataWriteError 让调用方中止本轮。

    `record_failure=False` 用于 find_existing 里给孤儿单补写键的场景：那条路径本来
    就是兜底修复，补不上也不该反过来把下一轮拦死。
    """
    kv = _metadata_kv(x)
    last = ""
    for attempt in range(retries):
        bad = []
        for k, v, t in kv:
            r = subprocess.run(["multica", "issue", "metadata", "set", issue_id,
                                "--key", k, "--value", v, "--type", t],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                bad.append(f"{k}: {r.stderr.strip()[:120]}")
        # 回读校验：写入返回 0 不代表键真的在单上（沿用 L 列回读确认的同款保险）。
        r = subprocess.run(["multica", "issue", "get", issue_id, "--output", "json"],
                           capture_output=True, text=True, timeout=120)
        got = {}
        if r.returncode == 0:
            s = r.stdout.find("{")
            got = (json.loads(r.stdout[s:]) if s >= 0 else {}).get("metadata") or {}
        missing = [k for k, v, _ in kv if str(got.get(k, "")).lower() != str(v).lower()]
        if not bad and not missing:
            return
        last = f"写入报错={bad or '无'} 回读缺失={missing}"
        if attempt < retries - 1:
            log(f"  metadata 写入未确认（{last}），{2 ** attempt}s 后重试")
            time.sleep(2 ** attempt)
    if record_failure:
        record_metadata_failure(issue_id, x, last)
    raise MetadataWriteError(
        f"{issue_id}（行{x['row']}）查重键写入失败：{last}。"
        f"已记进 {os.path.basename(METADATA_FAIL_FILE)}，人工补齐前不要再跑 --execute。"
        if record_failure else
        f"{issue_id}（行{x['row']}）查重键补写失败：{last}")


def create_issue(x: dict, kind: str, dry: bool) -> str | None:
    """建 Multica issue，返回 issue_id。

    **建单不派人**（项目主管 2026-09-07）：`--assignee-id` + `--status todo` 的组合
    会每建一张单就点起一次被指派 agent 的 run，和「一批全落地后做一轮集中分诊」的
    约定冲突；首次对存量跑 --execute 会一次点起一串。分诊统一人工做一轮。

    建单成功后立刻写 metadata 查重键——这是下一轮不重复建单的唯一依据，写不进去
    直接抛 MetadataWriteError 中止本轮，不允许留下查不到的单。"""
    title = ("[前端]" if x["cls"] == "frontend" else "[后端]" if x["cls"] == "backend" else "[待定]") \
        + x["title"]
    title = title[:60]
    priority = x["priority"]          # MYD-272：机械口径判定，不取原表 J 列
    if dry:
        log(f"[dry] 建单《{title}》 kind={kind} 优先级={priority}（{x['pri_why']}） "
            f"分配=不派人(待集中分诊) 附件={len(x['shots_local'])} "
            f"v2={(x['fp2'] or '—')[:12]} v1={x['fp1'][:12]}")
        return "dry-run-id"
    desc = build_description(x, kind)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8",
                                     dir=os.getcwd()) as tf:
        tf.write(desc)
        desc_path = tf.name
    cmd = ["multica", "issue", "create", "--title", title,
           "--description-file", desc_path, "--project", PROJECT_ID,
           "--status", "todo", "--allow-duplicate", "--output", "json"]
    if priority:
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
        issue_id = issue.get("id") or issue.get("identifier")
        if issue_id:
            set_issue_metadata(issue_id, x)
        return issue_id
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
    ap.add_argument("--no-dedup-check", action="store_true",
                    help="只读模式下跳过平台侧查重预演（不查 multica issue list，纯离线看清单）")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON 结果到 stdout")
    ap.add_argument("--execute", action="store_true",
                    help="真实建单 + 回写（需 L「丁」选项就绪；否则阻塞报错）。慎用。")
    ap.add_argument("--limit", type=int, default=0,
                    help="配合 --execute：只处理前 N 条做小样验证（0=全量）")
    ap.add_argument("--backend-digest", action="store_true",
                    help="建一张后端问题检查单（未解决+处理中，含好不好改粗评，@陆叙），不逐条建单。"
                         "配合 --execute 才真实建单，否则 dry 预览。")
    ap.add_argument("--date", default="", help="检查单日期(YYYY-MM-DD)，默认取本机当天")
    ap.add_argument("--writeback-on-create", action="store_true",
                    help="建单时同步回写飞书 L=丁+M=处理中（默认不回写，M 跟着真实指派走）")
    args = ap.parse_args()

    # 上一轮有单没写上查重键 → 它对前两层查重隐形。这里提前报警让人工补齐。
    # 只在同一台机器上连续跑时有效（文件会随 checkout 消失），跨轮次真正兜住这种
    # 孤儿单的是 find_existing 第三层的描述兜底，不是这个闸门。
    pending = check_pending_metadata_failures()
    if pending:
        log(f"❌ 阻塞：{len(pending)} 张单的查重键写入失败，未补齐前不建新单。"
            f"明细见 {METADATA_FAIL_FILE}")
        for p in pending:
            log(f"   - {p.get('issue')} 行{p.get('row')} 《{p.get('title')}》 {p.get('error','')[:120]}")
        log("   补法：multica issue metadata set <issue> --key <k> --value <v>；"
            f"补完删掉 {os.path.basename(METADATA_FAIL_FILE)} 再跑。")
        if args.execute:
            return 6
        log("   （只读模式继续，但 --execute 会被拒绝）")

    app_id, app_secret = os.environ.get("FEISHU_APP_ID"), os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        log("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return 1

    tokens = TokenManager(app_id, app_secret)
    probe_max_row(tokens)   # 行数每周在涨，写死会漏读尾部新行

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
        # 只读模式也跑一遍查重预演：--execute 前就能看清哪些行会被跳过、跳过的理由。
        if not args.no_dedup_check:
            cand = [x for x in items
                    if (x["cls"] != "uncertain" and x["difficulty"] == "easy"
                        and x["dev"].strip() not in OTHER_OWNERS)
                    or x["dev"].strip() in DING_VALUES]
            skips, fresh = [], []
            for x in cand:
                if x["undedupable"]:
                    skips.append({"row": x["row"], "title": x["title"],
                                  "reason": "无法安全查重(无截图 且 v1 与其它行相撞) → 转人工"})
                    continue
                try:
                    hit, key = find_existing(x)
                except RuntimeError as e:
                    log(f"查重预演中断：{e}")
                    break
                if hit:
                    skips.append({"row": x["row"], "title": x["title"], "key": key,
                                  "issue": f"{hit.get('identifier')}({hit.get('status')})",
                                  "reason": "已建过单"})
                else:
                    fresh.append(x)
            print_skips(skips)
            print(f"\n【--execute 将新建】 {len(fresh)} 条：")
            for x in fresh:
                print(f"  行{x['row']:>3} | {x['priority']:<6} | {x['title']}")
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
    # 不回写原表就不需要 L 选项就绪；只有回写时才做这项预检（项目主管 2026-09-07）。
    ding_val = ""
    if args.writeback_on_create:
        try:
            ding_val = resolve_ding_write_value(l_options)   # 选项不就绪直接抛 BlockedError
        except BlockedError as e:
            log(f"❌ 阻塞，未做任何写入：{e}")
            return 3
    log("真实模式：" + (f"L 将写选项「{ding_val}」" if args.writeback_on_create else "只建单，不回写原表 L/M")
        + (f"（仅前 {args.limit} 条小样）" if args.limit else "（全量）"))
    items, stats = collect(tokens, download=not args.no_download)
    # (1) 简单前端件：走正式处理单（原口径）。但 MYD-272 约束：L 列已经写了别人名字
    #     的行不抢（闫超/者俊/陈浩/冯志康 名下的 bug 不该由这条规则再派给丁）。
    easy = [x for x in items if x["cls"] != "uncertain" and x["difficulty"] == "easy"
            and x["dev"].strip() not in OTHER_OWNERS]
    # (2) 测试人员已在表里直接指派给【丁】、且未解决、尚未建单的问题：无论前后端难易，
    #     都建正式处理单跟踪调研+修复（陆叙 2026-08-13）。L 已是丁，仍回写 M=处理中 保证跨天幂等。
    ding_assigned = [x for x in items if x["dev"].strip() in DING_VALUES]
    # 合并去重（按 v2，无 v2 退 v1），保持 easy 在前的顺序。
    seen_fp, todo = set(), []
    for x in easy + ding_assigned:
        k = x["fp2"] or x["fp1"]
        if k in seen_fp:
            continue
        seen_fp.add(k)
        todo.append(x)
    if args.limit:
        todo = todo[:args.limit]
    log(f"本次候选 {len(todo)} 条正式处理单"
        f"（简单前端 {len(easy)} + 指派给丁 {len(ding_assigned)}，同轮去重后 {len(todo)}）；"
        f"其余不派人不回写。")
    state = load_state()
    skips: list[dict] = []
    done = 0
    for x in todo:
        # ① 既无截图又与别的行 v1 相撞 → 两个键都不能唯一定位，自动建单会污染，转人工。
        if x["undedupable"]:
            skips.append({"row": x["row"], "title": x["title"],
                          "reason": "无法安全查重(无截图 且 v1 与其它行相撞) → 转人工"})
            continue
        # ② 平台侧查重：v2 主键 → v1 兜底。查重本身失败就停，宁可不建也不重复建。
        try:
            hit, key = find_existing(x)
        except RuntimeError as e:
            log(f"❌ 查重失败，停止（已处理 {done} 条）：{e}")
            print_skips(skips)
            return 4
        if hit:
            skips.append({"row": x["row"], "title": x["title"], "key": key,
                          "issue": f"{hit.get('identifier')}({hit.get('status')})",
                          "reason": "已建过单"})
            log(f"  跳过(已建过) 行{x['row']} 键={key} → {hit.get('identifier')}")
            continue
        try:
            issue_id = create_issue(x, "formal", dry=False)
        except MetadataWriteError as e:
            log(f"❌ {e}")
            print_skips(skips)
            return 5
        if not issue_id:
            skips.append({"row": x["row"], "title": x["title"], "reason": "建单失败"})
            log(f"  建单失败，跳过回写：行{x['row']}")
            continue
        state[x["fp2"] or x["fp1"]] = issue_id
        save_state(state)   # 仅本轮内兜底；跨轮次真相在 issue metadata
        # 原表 L/M 是测试在看的信息源。建单 ≠ 有人开工，默认不回写「处理中」，
        # 免得往别人的看板里塞一个不准的状态（项目主管 2026-09-07）；
        # M 列应跟着真实指派走，分诊派人时再回写。
        if args.writeback_on_create:
            try:
                write_back_row(tokens, x["row"], ding_val, in_progress=True, dry=False)
            except BlockedError as e:
                log(f"❌ 行{x['row']} 回写阻塞（issue={issue_id} 已建），停止：{e}")
                print_skips(skips)
                return 3
        done += 1
        log(f"  ✓ 行{x['row']} issue={issue_id} 优先级={x['priority']} 未派人(待集中分诊)"
            + (f"，L={ding_val}/M={IN_PROGRESS}" if args.writeback_on_create else "，未回写原表"))
        time.sleep(0.3)
    print_skips(skips)   # MYD-272：每轮都要打全跳过明细，不允许静默跳过
    log(f"完成（真实）：新建 {done} 条，跳过 {len(skips)} 条，候选合计 {len(todo)}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
