"""
任务19：蚂蚁集团社招 AI PM 岗位采集
源：https://talent.antgroup.com/off-campus?categories=97
API：POST https://hrcareersweb.antgroup.com/api/social/position/search?ctoken=...
说明：API categories 参数无效（全量返回），需在 Playwright session 内分页拉取全量（~912条/30页），
     客户端按 categories 字段含 "产品类-" 过滤出产品岗
详情字段：list API 已含 description + requirement（完整 JD），无需单独请求详情
"""
import json, os, re, sys, time
from playwright.sync_api import sync_playwright
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
OUT_DIR = get_jobs_dir()
os.makedirs(OUT_DIR, exist_ok=True)

RAW_FILE  = "/Users/Shared/antgroup_social_raw.json"
OUT_FILE  = os.path.join(OUT_DIR, "antgroup_social_jobs.json")
EXCL_FILE = os.path.join(OUT_DIR, "antgroup_social_excluded.json")
BASE_URL  = "https://hrcareersweb.antgroup.com"
JOB_TMPL  = "https://talent.antgroup.com/off-campus/position/{id}?channel=official-website"

PAGE_SIZE = 30

# ── AI 关键词 ──────────────────────────────────────────────
AI_KW = re.compile(
    r"AI|人工智能|大模型|LLM|AIGC|智能|NLP|语音|视觉|推荐算法|"
    r"机器学习|深度学习|GPT|Copilot|Agent|RAG|向量|知识图谱|"
    r"自动驾驶|智驾|多模态|Prompt|具身|通义|百宝箱", re.I
)
AI_STRONG = re.compile(
    r"大模型|LLM|AIGC|AI(?:产品|平台|工具|系统|能力|功能|方向|策略|驱动|赋能)|"
    r"GPT|Copilot|RAG|向量数据库|智能体|Agent|智能客服|AI客服|"
    r"自动驾驶|通义|具身智能|多模态|百宝箱|AI开放平台|AI安全|AI原生", re.I
)
PROD_KW = re.compile(
    r"产品经理|产品专家|产品高级|产品负责|PM\b|产品规划|需求|PRD|产品设计|"
    r"产品架构|产品策略|产品体验|产品运营|产品研究", re.I
)

# ── Stage1 ─────────────────────────────────────────────────
TITLE_MUST = re.compile(r"产品|PM\b|Product|专家", re.I)
TITLE_EXCL = re.compile(
    r"工程师|研发|测试|运维|架构师|算法(?!产品)|数据科学|"
    r"设计师|视觉设计|UI\b|UX\b|前端|后端|Java|Python|C\+\+|"
    r"HR|人力资源|招聘(?!产品)|运营(?!产品)|销售|市场(?!产品)|"
    r"财务|法务|行政|客服(?!产品)|审计|BD\b|采购(?!产品)|"
    r"实习生|校招", re.I
)

# Stage2
TECH_HARD = re.compile(r"独立编码|写代码|代码实现|系统开发|系统架构|后端开发", re.I)
TECH_SOFT = re.compile(r"SQL|Python|技术背景|了解.*算法|熟悉.*开发|理解.*架构|接口联调", re.I)
PROD_SIG  = re.compile(r"产品经理|需求|PRD|用户故事|产品规划|原型|交互|用户体验|迭代|产品策略", re.I)

# Stage1 手动绕过（title_excl 误伤但确实是 AI PM）
STAGE1_BYPASS_IDS: set[str] = {
    "25121107972489",  # 客服助理智能体产品经理：智能体 = 强 AI，客服场景
    "26021008709366",  # AI 平台产品与运营负责人：AI 平台 PM 兼负责人
    "26051109931874",  # 智能运营平台产品经理：智能化产品，含 AI 信号
    "26040109402656",  # 智能运营平台产品专家：同上
}

# Stage3 手动排除
STAGE3_MANUAL_EXCL: set[str] = {
    "26012608540506",  # 商业产品经理（ZOLOZ）：AI工具=「善于使用AI工具」，AI是工具技能不是产品本身
    "26041709641071",  # 用增产品专家：AI产品=「深度使用AI产品」，用户视角非AI PM
}
# Stage3 手动纳入（AI_WEAK 但确实与 AI 相关）
STAGE3_KEEP_IDS: set[str] = {
    "26031609160573",  # 智能支付终端软件产品专家：智能终端产品
    "26010808303454",  # 金融AI云产品专家：AI云平台产品
    "25121007966905",  # AI商业化产品专家：AI 产品商业化
}

# ── Step 1: 全量抓取 ────────────────────────────────────────
def fetch_all() -> list[dict]:
    all_jobs = []
    total = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        # 1. Establish session and get ctoken from cookies
        page.goto("https://talent.antgroup.com/off-campus?categories=97",
                  wait_until="networkidle", timeout=50000)
        time.sleep(2)
        # ctoken is stored as a cookie named "ctoken" on .antgroup.com (parent domain)
        cookies = ctx.cookies()  # get ALL cookies, not filtered by URL
        ctoken = next((c["value"] for c in cookies if c["name"] == "ctoken"), None)
        print(f"  [Step1] ctoken: {ctoken}", flush=True)
        if not ctoken:
            raise ValueError("无法从 cookie 获取 ctoken，无法继续")

        # 2. Paginate through all jobs
        page_idx = 1
        while True:
            result = page.evaluate(f"""async () => {{
                const r = await fetch('{BASE_URL}/api/social/position/search?ctoken={ctoken}', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    credentials: 'include',
                    body: JSON.stringify({{
                        key: '', regions: '', categories: '', subCategories: '',
                        bgCode: '', socialQrCode: '',
                        pageIndex: {page_idx}, pageSize: {PAGE_SIZE},
                        channel: 'group_official_site', language: 'zh'
                    }})
                }});
                return await r.json();
            }}""")

            if not result.get("success"):
                print(f"  [Step1] page={page_idx} 失败: {result.get('errorMsg')}", flush=True)
                break

            items = result.get("content") or []
            if total is None:
                total = result.get("totalCount", 0)

            # Filter client-side: keep only 产品类 jobs
            prod_items = [j for j in items if any(
                str(c).startswith("产品类") for c in (j.get("categories") or [])
            )]
            all_jobs.extend(prod_items)
            print(f"  [Step1] page={page_idx} items={len(items)} prod={len(prod_items)} "
                  f"total_prod={len(all_jobs)}/{total}", flush=True)

            if len(all_jobs) > 0 and page_idx * PAGE_SIZE >= total:
                break
            if not items:
                break
            page_idx += 1
            time.sleep(0.5)

        browser.close()

    return all_jobs


# ── Step 2: 结构化 ─────────────────────────────────────────
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def structurize(raw: list[dict]) -> list[dict]:
    jobs = []
    for item in raw:
        job_id = str(item.get("id", ""))
        full_name = clean(item.get("name", ""))
        dept   = clean(item.get("department", ""))
        desc   = clean(item.get("description", ""))
        req    = clean(item.get("requirement", ""))
        jd     = f"{desc}\n{req}".strip()
        city   = ", ".join(item.get("workLocations") or [])
        cat    = ", ".join(item.get("categories") or [])
        url    = JOB_TMPL.format(id=job_id)

        # 名称格式多样：
        #   蚂蚁集团-{职位}-{业务线}
        #   蚂蚁数字科技-数字科技线-{职位}   ← 多一段 BU
        #   网商银行-{职位}-{地点}
        # 策略：若第二段不含职位信号词但第三段含，则取第三段作为职位名
        JOB_SIGNAL = re.compile(r"产品|经理|专家|PM|负责人|总监|助手|PD\b|analyst", re.I)
        parts = full_name.split("-")
        if len(parts) >= 3 and not JOB_SIGNAL.search(parts[1]) and JOB_SIGNAL.search(parts[2]):
            title = parts[2].strip()
            bu    = clean("-".join(parts[3:])) or parts[1].strip() or dept
        elif len(parts) >= 2:
            title = parts[1].strip()
            bu    = clean("-".join(parts[2:])) or dept
        else:
            title = full_name
            bu    = dept

        # keywords
        kws = list({m.group() for m in AI_KW.finditer(title + " " + jd[:500])})

        # summary: first sentence of description
        summary = re.split(r"[；;。\n]", desc)[0][:80] if desc else title

        jobs.append({
            "id":       job_id,
            "company":  "蚂蚁集团",
            "bu":       bu,
            "title":    title,
            "city":     city,
            "type":     "社招",
            "category": cat,
            "url":      url,
            "jd":       jd,
            "summary":  summary,
            "keywords": kws,
        })
    return jobs


# ── Stage1 ─────────────────────────────────────────────────
def stage1(jobs):
    ok, ex = [], []
    for j in jobs:
        title = j["title"]
        jd500 = j["jd"][:500]
        if j["id"] in STAGE1_BYPASS_IDS:
            ok.append(j)  # 手动绕过 title_excl
        elif TITLE_EXCL.search(title):
            j["excl_reason"] = "Stage1:title_excl"; ex.append(j)
        elif not TITLE_MUST.search(title):
            j["excl_reason"] = "Stage1:title_no_pm"; ex.append(j)
        elif not AI_KW.search(title) and not AI_KW.search(jd500):
            j["excl_reason"] = "Stage1:no_ai_kw"; ex.append(j)
        else:
            ok.append(j)
    return ok, ex


# ── Stage2 ─────────────────────────────────────────────────
def stage2(jobs):
    ok, ex = [], []
    for j in jobs:
        text = j["title"] + "\n" + j["jd"]
        tech_hard = len(TECH_HARD.findall(text))
        tech_soft = len(TECH_SOFT.findall(text))
        prod_sig  = len(PROD_SIG.findall(text))
        tech_score = tech_hard * 3 + tech_soft
        if tech_hard >= 2:
            j["excl_reason"] = f"Stage2:tech_hard={tech_hard}"; ex.append(j)
        elif tech_score >= 8 and prod_sig <= 2:
            j["excl_reason"] = f"Stage2:tech_score={tech_score},prod_sig={prod_sig}"; ex.append(j)
        else:
            ok.append(j)
    return ok, ex


# ── Stage3 ─────────────────────────────────────────────────
def stage3(jobs):
    ok, ex = [], []
    for j in jobs:
        text = j["title"] + "\n" + j["jd"]
        if j["id"] in STAGE3_MANUAL_EXCL:
            j["excl_reason"] = "Stage3:manual_excl"; ex.append(j)
        elif AI_STRONG.search(text) or j["id"] in STAGE3_KEEP_IDS:
            ok.append(j)
        else:
            j["excl_reason"] = "Stage3:weak_ai_only"; ex.append(j)
    return ok, ex


# ── URL 验证 ────────────────────────────────────────────────
def verify_urls(jobs):
    from urllib.request import Request, urlopen
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def check(j):
        try:
            req = Request(j["url"], headers={"User-Agent": UA})
            with urlopen(req, timeout=10) as r:
                return j["id"], r.status == 200
        except:
            return j["id"], False

    print("\n[URLVerify] 并发验证...", flush=True)
    bad = []
    with ThreadPoolExecutor(max_workers=8) as exe:
        for jid, ok in [f.result() for f in as_completed({exe.submit(check, j): j for j in jobs})]:
            if not ok:
                bad.append(jid)
    if bad:
        print(f"  ⚠️  不可访问: {bad}", flush=True)
    else:
        print(f"  ✓  全部 {len(jobs)} 条可访问", flush=True)


# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print("[任务19] 蚂蚁集团社招 AI PM 岗位", flush=True)

    if "--reuse-raw" in sys.argv and os.path.exists(RAW_FILE):
        print(f"[Step1] 复用 {RAW_FILE}", flush=True)
        raw = json.load(open(RAW_FILE))
    else:
        print("[Step1] Playwright 全量抓取（产品类过滤）...", flush=True)
        raw = fetch_all()
        json.dump(raw, open(RAW_FILE, "w"), ensure_ascii=False, indent=2)
        print(f"[Step1] 产品类 raw {len(raw)} 条 → {RAW_FILE}", flush=True)

    jobs_raw = structurize(raw)
    # 去重（同一 id 可能被采集多次）
    seen_ids = set()
    jobs = []
    for j in jobs_raw:
        if j["id"] not in seen_ids:
            seen_ids.add(j["id"])
            jobs.append(j)
    print(f"[Step2] 结构化 {len(jobs_raw)} 条，去重后 {len(jobs)} 条", flush=True)

    s1_ok, s1_ex = stage1(jobs)
    print(f"[Stage1] 保留 {len(s1_ok)}  排除 {len(s1_ex)}", flush=True)
    for j in s1_ex:
        print(f"  ✗ [{j['id']}] {j['title'][:60]}  ({j.get('excl_reason','')})", flush=True)

    s2_ok, s2_ex = stage2(s1_ok)
    print(f"[Stage2] 保留 {len(s2_ok)}  排除 {len(s2_ex)}", flush=True)
    for j in s2_ex:
        print(f"  ✗ [{j['id']}] {j['title'][:60]}  ({j.get('excl_reason','')})", flush=True)

    final, s3_ex = stage3(s2_ok)
    print(f"[Stage3] 保留 {len(final)}  排除 {len(s3_ex)}", flush=True)
    for j in s3_ex:
        print(f"  ✗ [{j['id']}] {j['title'][:60]}  ({j.get('excl_reason','')})", flush=True)

    verify_urls(final)

    json.dump(final,              open(OUT_FILE,  "w"), ensure_ascii=False, indent=2)
    json.dump(s1_ex+s2_ex+s3_ex, open(EXCL_FILE, "w"), ensure_ascii=False, indent=2)
    print(f"\n[Done] 保留 {len(final)}  排除 {len(s1_ex+s2_ex+s3_ex)}", flush=True)
    print(f"  → {OUT_FILE}", flush=True)

    print("\n[Step 1-8 完成情况]", flush=True)
    print(f"  ☑ 1 全量抓取 + raw 已保存", flush=True)
    print(f"  ☑ 2a keywords 字段已提取", flush=True)
    print(f"  ☑ 2b summary 字段已生成", flush=True)
    print(f"  ☑ 2c jd 字段完整原文", flush=True)
    print(f"  ☑ 3 Stage1 标题+JD过滤", flush=True)
    print(f"  ☑ 4 Stage2 tech/prod评分", flush=True)
    print(f"  ☑ 5 Stage3 二次自检", flush=True)
    print(f"  ☑ 6 URL验证", flush=True)
    print(f"  ☑ 7 存档 {OUT_FILE}", flush=True)

    print("\n[最终保留岗位]", flush=True)
    for j in final:
        ai_flag = "★" if AI_STRONG.search(j["title"] + j["jd"][:200]) else "  "
        print(f"  {ai_flag} [{j['id']}] {j['title'][:65]}  [{j['bu']}]", flush=True)


if __name__ == "__main__":
    main()
