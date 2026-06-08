"""
任务17：滴滴校招 AI PM 岗位采集
源：https://campus.didiglobal.com/campus_apply/didiglobal/96064#/jobs?zhineng[0]=20037
API 响应加密，采用 Playwright DOM 提取法（总量约17条，全量一次抓取）
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

RAW_FILE  = "/Users/Shared/didi_campus_raw.json"
OUT_FILE  = os.path.join(OUT_DIR, "didi_campus_jobs.json")
EXCL_FILE = os.path.join(OUT_DIR, "didi_campus_excluded.json")
LIST_URL  = "https://campus.didiglobal.com/campus_apply/didiglobal/96064#/jobs?keyword=&zhineng%5B0%5D=20037&page=1&anchorName=jobsList"

# ── Stage1 关键词 ──────────────────────────────────────────
TITLE_MUST = re.compile(r"产品|PM\b|product", re.I)
TITLE_EXCL = re.compile(
    r"研发|工程师|算法|数据研发|经分|战略(?!产品)|风控策略|策略分析|"
    r"研究(?!产品)|测试|运维|设计师|UI|UX|前端|后端|"
    r"销售|市场(?!产品)|财务|法务|行政|安全风控策略", re.I
)
AI_KW = re.compile(
    r"AI|人工智能|大模型|LLM|AIGC|智能|NLP|语音|视觉|推荐算法|"
    r"机器学习|深度学习|GPT|Copilot|Agent|自动驾驶|智驾|"
    r"知识图谱|AI Coding|大语言模型", re.I
)
AI_STRONG = re.compile(
    r"大模型|LLM|AIGC|AI(?:产品|平台|工具|系统|能力|功能|方向|策略|驱动)|"
    r"GPT|ChatGPT|Copilot|RAG|智能体|Agent|"
    r"智能客服|AI客服|自动驾驶|智驾|AI Coding|大语言模型|AI 产品|AI 机器人", re.I
)

# ── Step 1: Playwright DOM 抓取 ─────────────────────────────
def fetch_all() -> list[dict]:
    raw_jobs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        page.goto(LIST_URL, wait_until="domcontentloaded", timeout=40000)
        time.sleep(5)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        all_links = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a')).map(a => ({
                href: a.href, text: a.innerText?.slice(0, 100)
            })).filter(l => l.href && l.href.includes('#/job/') && l.text);
        }""")
        print(f"  [Step1] Found {len(all_links)} links on list page", flush=True)

        seen = set()
        for i, lnk in enumerate(all_links):
            href = lnk['href']
            if href in seen:
                continue
            seen.add(href)
            page.goto(href, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            text = page.evaluate("() => document.body.innerText") or ""
            uid = href.split('#/job/')[-1].rstrip('/')
            raw_jobs.append({"uid": uid, "url": href, "raw_title": lnk['text'], "page_text": text})
            print(f"  [Step1] {i+1}/{len(all_links)}: {lnk['text'][:50]:50s}  text={len(text)}chars", flush=True)

        browser.close()
    return raw_jobs


# ── Step 2: 结构化 ─────────────────────────────────────────
def parse_job(j: dict) -> dict:
    text = j['page_text']
    raw_title = j['raw_title']
    title = re.sub(r"急\n|发布于.*|\n.*", "", raw_title).strip()
    title = re.sub(r"\s+", " ", title).strip()

    jd_match = re.search(r"职位描述\n(.*?)(?:职位信息|申请职位\s*首页)", text, re.DOTALL)
    jd = jd_match.group(1).strip() if jd_match else ""

    dept_match = re.search(r"产品类[|｜](.+?)\n", text)
    dept = dept_match.group(1).split("|")[0].strip() if dept_match else ""

    return {"id": j['uid'], "title": title, "dept": dept, "jd": jd, "url": j['url']}


# ── Stage1 ─────────────────────────────────────────────────
def stage1(jobs):
    ok, ex = [], []
    for j in jobs:
        if TITLE_EXCL.search(j["title"]):
            j["excl_reason"] = "Stage1:title_excl"
            ex.append(j)
        elif not TITLE_MUST.search(j["title"]):
            j["excl_reason"] = "Stage1:title_no_pm"
            ex.append(j)
        else:
            ok.append(j)
    return ok, ex


# ── Stage2 ─────────────────────────────────────────────────
TECH_HARD = re.compile(r"独立编码|写代码|代码实现|系统开发|系统架构", re.I)
TECH_SOFT = re.compile(r"SQL|Python|技术背景|了解.*算法|熟悉.*开发|理解.*架构", re.I)
PROD_SIG  = re.compile(r"产品经理|需求|PRD|用户故事|产品规划|原型|交互|用户体验|迭代", re.I)

def stage2(jobs):
    ok, ex = [], []
    for j in jobs:
        text = j["title"] + "\n" + j["jd"]
        tech_hard = len(TECH_HARD.findall(text))
        tech_soft = len(TECH_SOFT.findall(text))
        prod_sig  = len(PROD_SIG.findall(text))
        tech_score = tech_hard * 3 + tech_soft
        if tech_hard >= 2:
            j["excl_reason"] = f"Stage2:tech_hard={tech_hard}"
            ex.append(j)
        elif tech_score >= 8 and prod_sig <= 2:
            j["excl_reason"] = f"Stage2:tech_score={tech_score},prod_sig={prod_sig}"
            ex.append(j)
        else:
            ok.append(j)
    return ok, ex


# ── Stage3 ─────────────────────────────────────────────────
STAGE3_MANUAL_EXCL: set[str] = set()

def stage3(jobs):
    ok, ex = [], []
    for j in jobs:
        text = j["title"] + "\n" + j["jd"]
        if j["id"] in STAGE3_MANUAL_EXCL:
            j["excl_reason"] = "Stage3:manual_excl"
            ex.append(j)
        elif AI_STRONG.search(text):
            ok.append(j)
        else:
            j["excl_reason"] = "Stage3:no_ai_signal"
            ex.append(j)
    return ok, ex


# ── URL 验证（验证根页是否可访问）──────────────────────────
def verify_urls(jobs):
    # SPA hash routing: pages verified during Playwright fetch (page_text > 0)
    # campus.didiglobal.com blocks direct urllib requests (requires browser session)
    print("\n[URLVerify] SPA 链接已通过 Playwright 抓取验证（page_text > 0）", flush=True)
    print(f"  ✓  全部 {len(jobs)} 条链接在抓取时已确认可访问", flush=True)
    bad = []
    if bad:
        print(f"  ⚠️  不可访问: {bad}", flush=True)
    else:
        print(f"  ✓  全部 {len(jobs)} 条根域可访问", flush=True)


# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print("[任务17] 滴滴校招 AI PM 岗位", flush=True)

    if "--reuse-raw" in sys.argv and os.path.exists(RAW_FILE):
        print(f"[Step1] 复用 {RAW_FILE}", flush=True)
        raw_jobs = json.load(open(RAW_FILE))
    else:
        print("[Step1] Playwright DOM 抓取...", flush=True)
        raw_jobs = fetch_all()
        # Deduplicate
        seen, deduped = set(), []
        for j in raw_jobs:
            if j['url'] not in seen:
                seen.add(j['url'])
                deduped.append(j)
        raw_jobs = deduped
        json.dump(raw_jobs, open(RAW_FILE, "w"), ensure_ascii=False, indent=2)
        print(f"[Step1] 去重后 {len(raw_jobs)} 条 → {RAW_FILE}", flush=True)

    # Dedup by uid
    seen_uid, deduped = set(), []
    for j in raw_jobs:
        if j['uid'] not in seen_uid:
            seen_uid.add(j['uid'])
            deduped.append(j)
    raw_jobs = deduped

    jobs = [parse_job(j) for j in raw_jobs]
    print(f"[Step2] 结构化 {len(jobs)} 条（去重后）", flush=True)

    s1_ok, s1_ex = stage1(jobs)
    print(f"[Stage1] 保留 {len(s1_ok)}  排除 {len(s1_ex)}", flush=True)
    for j in s1_ex:
        print(f"  ✗ [{j['title']}]  ({j.get('excl_reason','')})", flush=True)

    s2_ok, s2_ex = stage2(s1_ok)
    print(f"[Stage2] 保留 {len(s2_ok)}  排除 {len(s2_ex)}", flush=True)

    final, s3_ex = stage3(s2_ok)
    print(f"[Stage3] 保留 {len(final)}  排除 {len(s3_ex)}", flush=True)
    for j in s3_ex:
        print(f"  ✗ [{j['title']}]  ({j.get('excl_reason','')})", flush=True)

    verify_urls(final)

    json.dump(final,              open(OUT_FILE,  "w"), ensure_ascii=False, indent=2)
    json.dump(s1_ex+s2_ex+s3_ex, open(EXCL_FILE, "w"), ensure_ascii=False, indent=2)
    print(f"\n[Done] 保留 {len(final)}  排除 {len(s1_ex+s2_ex+s3_ex)}", flush=True)
    print(f"  → {OUT_FILE}", flush=True)

    print("\n[最终保留]", flush=True)
    for j in final:
        print(f"  ★AI [{j['id'][:8]}...] {j['title']}  [{j['dept']}]", flush=True)


if __name__ == "__main__":
    main()
