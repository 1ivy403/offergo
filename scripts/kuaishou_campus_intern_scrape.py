#!/usr/bin/env python3
"""
快手校招实习岗位抓取与筛选脚本
来源：https://campus.kuaishou.cn/recruit/campus/e/#/campus/jobs
     ?positionCategoryCodes=production,J1021,J1026,J1022,J1023,J1024,J1025
     &positionNatureCode=intern
"""
import json, re, time, os, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


# ── 常量 ────────────────────────────────────────────────────────────────────
API = "https://campus.kuaishou.cn/recruit/campus/e/api/v1/open/positions/simple"
ROOT_URL = "https://campus.kuaishou.cn/recruit/campus/e/"
DETAIL_TMPL = "https://campus.kuaishou.cn/recruit/campus/e/#/campus/jobs/{code}"
OUT_DIR = get_jobs_dir()
RAW_FILE = os.path.join(OUT_DIR, "kuaishou_campus_intern_raw.json")
OUT_FILE = os.path.join(OUT_DIR, "kuaishou_campus_intern_jobs.json")
EXCL_FILE = os.path.join(OUT_DIR, "kuaishou_campus_intern_excluded.json")
os.makedirs(OUT_DIR, exist_ok=True)

HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://campus.kuaishou.cn/",
    "Origin":  "https://campus.kuaishou.cn",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124",
}

# 全部产品分类 code（已在 URL 中指定）
CATEGORY_CODES = ["production", "J1021", "J1026", "J1022", "J1023", "J1024", "J1025"]
# 当前活跃的招募子项目 code（两个）
SUB_PROJECTS = ["20261749721165", "20271772783534"]

# ── 标题硬排除 ───────────────────────────────────────────────────────────────
EXCL_TITLE = re.compile(
    r"(工程师|研发|算法|开发|架构|测试|运维|SRE|DBA|安全|网络|硬件|"
    r"设计师|设计|UX|UI|前端|后端|客户端|移动端|"
    r"数据分析师|分析师|BI|科学家|研究员|实验员|"
    r"法务|财务|会计|行政|人事|HR|招聘|"
    r"市场|品牌|公关|PR|媒体|"
    r"销售|客服|内容运营|社区运营|活动运营)",
    re.I,
)
# 豁免（技术产品经理等）
EXCL_EXEMPT = re.compile(r"技术产品|AI.*产品|产品.*AI|智能.*产品|产品.*智能", re.I)

# ── AI/产品关键词 ────────────────────────────────────────────────────────────
AI_KWORDS = [
    "AI", "人工智能", "大模型", "LLM", "GPT", "AIGC", "NLP", "语音", "语义",
    "机器学习", "深度学习", "算法", "推荐系统", "搜索", "向量", "Embedding",
    "智能", "Agent", "多模态", "生成式", "知识图谱", "RAG", "强化学习",
    "自然语言", "计算机视觉", "OCR", "ASR", "TTS", "意图", "问答",
]
PROD_KWORDS = [
    "产品经理", "产品设计", "需求分析", "需求文档", "PRD", "原型", "用户研究",
    "用户体验", "用户调研", "用户增长", "产品规划", "产品迭代", "产品策略",
    "产品运营", "商业化", "生态", "Axure", "Figma",
]

def has_ai_word(text: str) -> bool:
    tl = text.lower()
    return any(w.lower() in tl for w in AI_KWORDS)

def has_product_word(text: str) -> bool:
    tl = text.lower()
    for w in PROD_KWORDS:
        if w.lower() in tl:
            return True
    # PM 必须整词
    if re.search(r"(?<![A-Za-z])PM(?![A-Za-z])", text):
        return True
    return False

def kw_extr(text: str, max_k: int = 8) -> list:
    """从 JD 文本提取最多 max_k 个关键词，避免无限循环"""
    if not text:
        return []
    out, seen = [], set()
    ordered = AI_KWORDS + PROD_KWORDS + ["产品", "策略", "数据", "增长", "平台", "电商", "海外"]
    for w in ordered:
        if len(out) >= max_k:
            break
        if w.lower() in text.lower() and w not in seen:
            seen.add(w)
            out.append(w)
    return out[:max_k]

# ── Step 1：抓取全量数据 ─────────────────────────────────────────────────────
def fetch_all() -> list:
    reuse = "--reuse-raw" in sys.argv
    if reuse and os.path.exists(RAW_FILE):
        print(f"[Step1] 复用已有 raw 数据：{RAW_FILE}", flush=True)
        with open(RAW_FILE) as f:
            return json.load(f)

    body = {
        "recruitSubProjectCodes": SUB_PROJECTS,
        "pageSize": 200,
        "pageNum": 1,
        "positionNatureCode": "intern",
        "positionCategoryCodes": CATEGORY_CODES,
    }
    req = Request(API, data=json.dumps(body).encode(), method="POST", headers=HEADERS)
    with urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    items = (data.get("result") or {}).get("list") or []
    total = (data.get("result") or {}).get("total") or 0
    print(f"[Step1] API total={total}  fetched={len(items)}", flush=True)

    with open(RAW_FILE, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[Step1] raw 已存 → {RAW_FILE}", flush=True)
    return items

# ── Step 2：结构化字段 ────────────────────────────────────────────────────────
def structurize(raw: list) -> list:
    jobs = []
    for it in raw:
        jd_parts = []
        desc = (it.get("description") or "").strip()
        demand = (it.get("positionDemand") or "").strip()
        if desc:
            jd_parts.append("【岗位职责】\n" + desc)
        if demand:
            jd_parts.append("【任职要求】\n" + demand)
        jd_full = "\n\n".join(jd_parts)

        locs = []
        for ld in (it.get("workLocationDicts") or []):
            if isinstance(ld, dict):
                locs.append(ld.get("name") or ld.get("code") or "")
        loc_str = "、".join(l for l in locs if l) or it.get("workLocationCode") or "未知"

        kws = kw_extr(jd_full)
        # summary：基于第一条职责简短提炼
        first_line = desc.split("\n")[0].strip() if desc else ""
        summary = re.sub(r"^[0-9、．.\s]+", "", first_line)[:80] if first_line else it.get("name")

        jobs.append({
            "id": str(it.get("id") or ""),
            "code": it.get("code") or "",
            "title": it.get("name") or "",
            "location": loc_str,
            "category": it.get("positionCategoryCode") or "",
            "type": "实习",
            "url": DETAIL_TMPL.format(code=it.get("code") or ""),
            "keywords": kws,
            "summary": summary,
            "jd": jd_full,
        })
    return jobs

# ── Stage1：标题 + JD 前 500 字粗筛 ─────────────────────────────────────────
def stage1(jobs: list) -> tuple:
    retained, excluded = [], []
    for j in jobs:
        title = j["title"]
        jd500 = j["jd"][:500]
        reason = None

        # 标题硬排除（豁免优先）
        if EXCL_TITLE.search(title) and not EXCL_EXEMPT.search(title):
            reason = f"Stage1-title-excl: {EXCL_TITLE.search(title).group()}"
        # 必须含产品词
        elif not has_product_word(title) and not has_product_word(jd500):
            reason = "Stage1-no-product-word"

        if reason:
            j["stage1_reason"] = reason
            excluded.append(j)
        else:
            retained.append(j)
    return retained, excluded

# ── Stage2：技术/产品信号评分 ────────────────────────────────────────────────
TECH_HARD = re.compile(
    r"(开发|编程|代码|工程|架构|部署|训练模型|model\s*train|算法工程|研发|研究员)", re.I)
TECH_SOFT = re.compile(
    r"(推荐系统|搜索引擎|AI|人工智能|大模型|NLP|机器学习|深度学习|LLM|智能|Agent)", re.I)
PROD_SIG  = re.compile(
    r"(产品经理|需求.*分析|用户.*研究|PRD|产品设计|用户.*体验|产品.*规划|路线图|Roadmap"
    r"|原型|Axure|商业化|产品策略|产品迭代)", re.I)

def score(jd: str) -> dict:
    tech_hard = len(TECH_HARD.findall(jd))
    tech_soft = len(TECH_SOFT.findall(jd))
    prod      = len(PROD_SIG.findall(jd))
    return {"tech_hard": tech_hard, "tech_soft": tech_soft, "prod": prod}

def stage2(jobs: list) -> tuple:
    retained, excluded = [], []
    for j in jobs:
        sc = score(j["jd"])
        j["score"] = sc
        # 硬技术词 ≥ 5 且产品词 = 0 → 排除
        if sc["tech_hard"] >= 5 and sc["prod"] == 0:
            j["stage2_reason"] = f"tech_hard={sc['tech_hard']} prod=0"
            excluded.append(j)
        # 完全无产品词且无 AI 关键词 → 排除
        elif sc["prod"] == 0 and sc["tech_soft"] == 0:
            j["stage2_reason"] = "no-prod-no-ai-signal"
            excluded.append(j)
        else:
            retained.append(j)
    return retained, excluded

# ── Stage3：手工精细化复核（边界案例） ───────────────────────────────────────
STAGE3_EXCLUDE_IDS: set[str] = {
    # 无 AI 信号的通用 PM 岗
    "12544",  # 用户产品经理-音乐方向（仅"了解AI发展趋势"，不作为AI PM岗）
    "11457",  # 平台产品经理-电商产品（纯电商，无AI信号）
    "11301",  # 策略产品经理-商业化方向（纯商业化，无AI信号）
    "11316",  # 产品运营-商业产品（运营岗，无AI信号）
    "11463",  # 平台产品经理-商业产品（纯商业平台，无AI信号）
    "11583",  # 用户产品经理（通用用户PM，无AI信号）
    "11508",  # 数据产品经理（大数据BI型，无AI信号）
    "11472",  # 平台产品经理-支付产品（纯支付平台，无AI信号）
    "11455",  # 平台产品经理-本地生活（本地生活，无AI信号）
    "11379",  # 海外产品经理（无AI信号）
    "11384",  # 海外电商产品经理（无AI信号）
}

def stage3(jobs: list) -> tuple:
    retained, excluded = [], []
    for j in jobs:
        if j["id"] in STAGE3_EXCLUDE_IDS:
            j["stage3_reason"] = "手工排除"
            excluded.append(j)
        else:
            retained.append(j)
    return retained, excluded

# ── Step 6：链接验证 ──────────────────────────────────────────────────────────
def verify_urls(jobs: list):
    """SPA 页面：验证根路径 200 即可（所有岗位共用同一 HTML）"""
    print(f"[Step6] SPA 根路径验证: {ROOT_URL}", flush=True)
    try:
        req = Request(ROOT_URL, method="GET", headers={"User-Agent": HEADERS["User-Agent"]})
        with urlopen(req, timeout=15) as r:
            code = r.status
        print(f"[Step6] root URL → HTTP {code}", flush=True)
        for j in jobs:
            j["url_ok"] = (code == 200)
    except Exception as e:
        print(f"[Step6] root URL 验证失败: {e}", flush=True)
        for j in jobs:
            j["url_ok"] = False

# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print("[快手校招实习] 开始执行 aipmjobs 工作流", flush=True)

    # Step 1
    raw = fetch_all()
    print(f"[Step1] 共 {len(raw)} 条原始岗位", flush=True)

    # Step 2
    jobs = structurize(raw)
    print(f"[Step2] 结构化完成：{len(jobs)} 条", flush=True)

    # Step 3 Stage1
    s1_ok, s1_ex = stage1(jobs)
    print(f"[Stage1] 保留 {len(s1_ok)}  排除 {len(s1_ex)}", flush=True)
    for j in s1_ex:
        print(f"  ✗ {j['title']}  ({j.get('stage1_reason','')})", flush=True)

    # Step 4 Stage2
    s2_ok, s2_ex = stage2(s1_ok)
    print(f"[Stage2] 保留 {len(s2_ok)}  排除 {len(s2_ex)}", flush=True)
    for j in s2_ex:
        print(f"  ✗ {j['title']}  ({j.get('stage2_reason','')})", flush=True)

    # Step 5 Stage3
    final_ok, s3_ex = stage3(s2_ok)
    print(f"[Stage3] 保留 {len(final_ok)}  排除 {len(s3_ex)}", flush=True)
    for j in s3_ex:
        print(f"  ✗ {j['title']}  ({j.get('stage3_reason','')})", flush=True)

    # Step 6
    verify_urls(final_ok)

    # Step 7
    all_excluded = s1_ex + s2_ex + s3_ex
    with open(OUT_FILE, "w") as f:
        json.dump(final_ok, f, ensure_ascii=False, indent=2)
    with open(EXCL_FILE, "w") as f:
        json.dump(all_excluded, f, ensure_ascii=False, indent=2)

    print(f"\n[Done] 最终保留：{len(final_ok)} 条  排除：{len(all_excluded)} 条", flush=True)
    print(f"  结果 → {OUT_FILE}", flush=True)
    print(f"  排除 → {EXCL_FILE}", flush=True)

    print("\n[最终保留岗位]", flush=True)
    for j in final_ok:
        ai_flag = "★AI★" if has_ai_word(j["title"] + j["jd"][:300]) else "   "
        print(f"  {ai_flag} [{j['id']}] {j['title']}", flush=True)

if __name__ == "__main__":
    main()
