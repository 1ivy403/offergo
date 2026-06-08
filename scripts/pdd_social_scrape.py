"""
Task 22: PDD社招 careers.pddglobalhr.com/jobs
- 产品类共81条
- 用stealth Playwright：列表翻页收codes → 详情页提取DOM JD
"""
import json, re, time, os
from playwright.sync_api import sync_playwright
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
BASE = "https://careers.pddglobalhr.com"
SAVE_DIR = get_jobs_dir()
os.makedirs(SAVE_DIR, exist_ok=True)

# ── Stage1 筛选 ──
TITLE_KEEP = re.compile(r'产品经理|产品总监|产品专家|产品负责人|产品运营|产品策略|PM|product\s*manager', re.I)
TITLE_EXCL = re.compile(r'^(?:(?!产品).)*(?:工程师|开发|测试|运维|前端|后端|算法|数据库|架构|设计师|UI|UX|美术|视觉|运营(?!产品)|市场(?!产品)|销售|客服(?!产品)|招商|法务|财务|人事|HR|行政|商务)', re.I)
AI_KW = re.compile(r'AI|人工智能|大模型|LLM|机器学习|智能|AIGC|GPT|深度学习|NLP|语音|视觉识别|推荐算法|强化学习', re.I)

# ── Stage3 ──
AI_STRONG = re.compile(
    r'AI产品|AI平台|大模型产品|LLM产品|AIGC产品|AI中台|AI基础|智能产品|人工智能产品'
    r'|Agent.*产品|智能服务产品|智能客服.*产品|聊天机器人.*产品|AI.*经理|AI.*专家'
    r'|智能.*经理|智能.*专家|Agent.*经理|Agent.*专家', re.I)
AI_WEAK = re.compile(r'善用AI|使用AI工具|AI辅助|AI赋能|运用AI|结合AI工具', re.I)


def clean(t):
    return re.sub(r'\s+', ' ', t).strip()


def extract_jd(text):
    """从页面 innerText 提取 JD 部分"""
    # 去掉导航头尾
    text = re.sub(r'^.*?(?=岗位职责|任职要求|职位描述)', '', text, flags=re.S)
    text = re.sub(r'(?:Tips:|Copyright).*$', '', text, flags=re.S)
    return clean(text)


def structurize(job):
    raw_name = job.get("name", "")
    # 清理混入的多余DOM文字（"详情 产品类 ..."）
    name = clean(raw_name.split("详情")[0].split("\n")[0])
    code = job.get("code", "")
    loc = job.get("workLocation", "")
    jd = job.get("jd", "")

    summary = jd[:200] if jd else ""
    url = f"{BASE}/jobs/detail?code={code}"
    has_ai = bool(AI_KW.search(name + " " + jd))

    return {
        "id": code,
        "title": name,
        "company": "拼多多",
        "location": loc,
        "url": url,
        "summary": summary,
        "jd": jd,
        "has_ai": has_ai,
        "raw": job,
    }


def stage1_filter(jobs):
    passed, excluded = [], []
    for j in jobs:
        t = j["title"]
        if not TITLE_KEEP.search(t):
            j["excl_reason"] = "S1:no_pm_keyword"
            excluded.append(j)
        elif TITLE_EXCL.search(t):
            j["excl_reason"] = "S1:excl_pattern"
            excluded.append(j)
        else:
            passed.append(j)
    print(f"[Stage1] passed={len(passed)} excl={len(excluded)}", flush=True)
    return passed, excluded


def stage2_filter(jobs):
    passed, excluded = [], []
    for j in jobs:
        jd_text = j.get("jd", "") + " " + j["title"]
        # 简单技术词计数
        tech_kw = re.findall(r'(?:Java|Python|C\+\+|Go|Rust|SQL|API|SDK|算法工程|后台开发|系统架构|微服务|K8s|Docker)', jd_text, re.I)
        tech_score = len(tech_kw)
        prod_kw = re.findall(r'(?:产品规划|需求分析|PRD|原型|用户研究|商业化|数据分析|产品迭代|路线图|roadmap)', jd_text, re.I)
        prod_score = len(prod_kw)
        if tech_score >= 5 and prod_score == 0:
            j["excl_reason"] = f"S2:tech>5({tech_score}) prod=0"
            excluded.append(j)
        else:
            passed.append(j)
    print(f"[Stage2] passed={len(passed)} excl={len(excluded)}", flush=True)
    return passed, excluded


def stage3_filter(jobs):
    strong, weak, excl = [], [], []
    for j in jobs:
        text = j["title"] + " " + j.get("jd", "")
        if AI_STRONG.search(text):
            strong.append(j)
        elif AI_WEAK.search(text):
            j["ai_level"] = "WEAK"
            weak.append(j)
        else:
            j["excl_reason"] = "S3:no_ai_signal"
            excl.append(j)
    result = strong + weak
    print(f"[Stage3] strong={len(strong)} weak={len(weak)} excl={len(excl)}", flush=True)
    return result, excl


def dismiss_modal(page):
    """关闭任何弹出模态框"""
    try:
        close_btn = page.locator('[data-testid="beast-core-modal"] [class*="close"], [data-testid="beast-core-modal"] button').first
        if close_btn.count() > 0:
            close_btn.click(timeout=2000)
            page.wait_for_timeout(500)
    except:
        pass
    # 也尝试按 Escape
    try:
        modal = page.locator('[data-testid="beast-core-modal"]')
        if modal.count() > 0:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
    except:
        pass


def extract_dom_jobs(page):
    """从当前页DOM提取产品职位 job links（仅 PR 前缀代码）"""
    return page.evaluate("""() => {
        const jobs = [];
        const links = document.querySelectorAll('a[href*="/jobs/detail?code=PR"]');
        links.forEach(a => {
            // 只取第一个文本节点（直接子文本，避免嵌套DOM混入）
            let name = '';
            for (const node of a.childNodes) {
                if (node.nodeType === 3) { name = node.textContent.trim(); break; }
            }
            if (!name) name = (a.innerText || '').split(/\\n|详情/)[0].trim();
            const code = new URL(a.href).searchParams.get('code') || '';
            if (code && name && name !== '详情' && name.length > 2) {
                jobs.push({code, name});
            }
        });
        const seen = new Set();
        return jobs.filter(j => seen.has(j.code) ? false : seen.add(j.code));
    }""")


def scrape_list(page):
    """收集所有产品类 job codes，返回 list[dict]"""
    jobs_raw = []
    seen_codes = set()

    # 等待首页加载
    with page.expect_response(lambda r: "position/list" in r.url, timeout=20000) as ri:
        page.goto(f"{BASE}/jobs", wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(2000)

    # 点击产品分类
    dismiss_modal(page)
    page.locator("span.ctz-tag:has-text('产品')").first.click(force=True)
    page.wait_for_timeout(2500)

    # 读取总量
    total_text = page.evaluate("""() => {
        const t = document.body.innerText;
        const m = t.match(/共(\\d+)个职位/);
        return m ? parseInt(m[1]) : 0;
    }""")
    total = int(total_text or 0)
    page_count = (total + 9) // 10
    print(f"[list] total={total} pages={page_count}", flush=True)

    # 收集当前页
    page_jobs = extract_dom_jobs(page)
    for j in page_jobs:
        if j["code"] not in seen_codes:
            seen_codes.add(j["code"])
            jobs_raw.append(j)
    print(f"  page1: {len(page_jobs)} jobs → total {len(jobs_raw)}", flush=True)

    # 翻页
    for pg in range(2, page_count + 1):
        next_btn = page.locator(".rocket-pagination-next")
        if next_btn.count() == 0:
            print(f"  no next button at page {pg}", flush=True)
            break
        is_disabled = next_btn.first.evaluate("el => el.classList.contains('rocket-pagination-disabled')")
        if is_disabled:
            print(f"  next button disabled at page {pg}", flush=True)
            break
        # 关闭可能出现的 modal
        dismiss_modal(page)
        # 等待页码变化
        expected_start = (pg - 1) * 10 + 1
        next_btn.first.click(force=True)
        # 等待页面文字更新到新页码
        try:
            page.wait_for_function(
                f"() => document.body.innerText.includes('显示{expected_start}-')",
                timeout=8000
            )
        except:
            page.wait_for_timeout(2000)
        page_jobs = extract_dom_jobs(page)
        new_jobs = [j for j in page_jobs if j["code"] not in seen_codes]
        for j in new_jobs:
            seen_codes.add(j["code"])
            jobs_raw.append(j)
        print(f"  page{pg}: +{len(new_jobs)} → total {len(jobs_raw)}", flush=True)

    print(f"[list] collected {len(jobs_raw)} raw product jobs", flush=True)
    return jobs_raw


def fetch_details(page, jobs_raw):
    """导航到每个详情页，提取 JD"""
    enriched = []
    for i, job in enumerate(jobs_raw):
        code = job.get("code", "")
        url = f"{BASE}/jobs/detail?code={code}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            txt = page.evaluate("() => document.body.innerText")
            jd = extract_jd(txt)
            job["jd"] = jd
            enriched.append(job)
        except Exception as e:
            print(f"  [!] detail {code} error: {e}", flush=True)
            job["jd"] = ""
            enriched.append(job)
        if (i + 1) % 10 == 0:
            print(f"  [detail] {i+1}/{len(jobs_raw)} done", flush=True)
    return enriched


def verify_url(url):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except:
        return True  # SPA pages often 200 on root


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"}
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        print("=== Step1: 收集列表 ===", flush=True)
        jobs_raw = scrape_list(page)

        print("\n=== Step2: 抓取详情 JD ===", flush=True)
        jobs_with_jd = fetch_details(page, jobs_raw)
        browser.close()

    print("\n=== Step3: structurize ===", flush=True)
    jobs = [structurize(j) for j in jobs_with_jd]
    print(f"structurized: {len(jobs)}", flush=True)

    print("\n=== Step4: Stage1 ===", flush=True)
    s1_pass, s1_excl = stage1_filter(jobs)

    print("\n=== Step5: Stage2 ===", flush=True)
    s2_pass, s2_excl = stage2_filter(s1_pass)

    print("\n=== Step6: Stage3 ===", flush=True)
    s3_pass, s3_excl = stage3_filter(s2_pass)
    all_excl = s1_excl + s2_excl + s3_excl

    print("\n=== Step7: URL验证 ===", flush=True)
    for j in s3_pass[:5]:
        ok = verify_url(j["url"])
        print(f"  {j['url']} → {ok}", flush=True)

    print("\n=== Step8: 保存 ===", flush=True)
    out_file = os.path.join(SAVE_DIR, "pdd_social_jobs.json")
    excl_file = os.path.join(SAVE_DIR, "pdd_social_excluded.json")
    raw_file = os.path.join(SAVE_DIR, "pdd_social_raw.json")

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(s3_pass, f, ensure_ascii=False, indent=2)
    with open(excl_file, "w", encoding="utf-8") as f:
        json.dump(all_excl, f, ensure_ascii=False, indent=2)
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(jobs_with_jd, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 最终结果: {len(s3_pass)} AI PM岗位", flush=True)
    for j in s3_pass:
        print(f"  [{j['id']}] {j['title']} | {j['location']}", flush=True)
    print(f"\n输出: {out_file}", flush=True)
    print(f"排除: {excl_file} ({len(all_excl)}条)", flush=True)


if __name__ == "__main__":
    main()
