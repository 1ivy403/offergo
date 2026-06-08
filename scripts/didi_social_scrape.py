"""
任务16：滴滴社招 AI PM 岗位采集
源：https://talent.didiglobal.com/social/list/1?jobType=3
列表 API：GET /recruit-portal-service/api/job/front/list?jobType=3&page=N&recruitType=1&size=16 (需 Playwright session)
详情 API：GET /recruit-portal-service/api/job/front/view/{jdId} (可直接 urllib)
"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import URLError
from playwright.sync_api import sync_playwright
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
OUT_DIR = get_jobs_dir()
os.makedirs(OUT_DIR, exist_ok=True)

RAW_FILE  = "/Users/Shared/didi_social_raw.json"
OUT_FILE  = os.path.join(OUT_DIR, "didi_social_jobs.json")
EXCL_FILE = os.path.join(OUT_DIR, "didi_social_excluded.json")
LIST_URL  = "https://talent.didiglobal.com/recruit-portal-service/api/job/front/list"
DETAIL_TMPL = "https://talent.didiglobal.com/recruit-portal-service/api/job/front/view/{}"
JOB_TMPL    = "https://talent.didiglobal.com/social/p/{}"

# ── Stage1 关键词 ──────────────────────────────────────────
TITLE_MUST = re.compile(
    r"产品|PM\b|product", re.I
)
TITLE_EXCL = re.compile(
    r"工程师|开发|测试|运维|架构师|算法|数据科学|设计师|设计[^总]|UI|UX|"
    r"前端|后端|Java|Python|C\+\+|Go\b|Rust|安全专家|安全工程|"
    r"hr|运营(?!产品)|销售|市场(?!产品)|财务|法务|行政|客服(?!产品)|"
    r"司机|骑手|外卖|配送", re.I
)
AI_KW = re.compile(
    r"AI|人工智能|大模型|LLM|AIGC|智能|NLP|语音|视觉|推荐算法|"
    r"机器学习|深度学习|GPT|ChatGPT|Copilot|Agent|RAG|向量|"
    r"知识图谱|自动驾驶|无人驾驶|智驾|智能驾驶|算法产品|"
    r"数据智能|智能运营|智能客服|智能风控", re.I
)
PROD_KW = re.compile(
    r"产品经理|产品负责人|产品总监|PM|产品规划|需求分析|产品设计|"
    r"用户研究|竞品|产品迭代|产品运营|产品架构|产品战略|产品体验", re.I
)

# ── Stage2 评分 ────────────────────────────────────────────
TECH_HARD = re.compile(
    r"独立编码|写代码|代码实现|系统开发|系统架构|后端开发|"
    r"微服务开发|API开发|框架开发|平台开发", re.I
)
TECH_SOFT = re.compile(
    r"SQL|Python|技术背景|了解.*算法|熟悉.*开发|理解.*架构|"
    r"熟悉API|接口联调|数据分析", re.I
)
PROD_SIG = re.compile(
    r"产品经理|需求|PRD|用户故事|产品规划|原型|交互|用户体验|"
    r"迭代|版本|商业化|产品策略|产品目标|数据驱动产品", re.I
)

# ── Stage3：只保留有 AI_STRONG 信号或被明确纳入的 AI_WEAK 岗 ─
AI_STRONG = re.compile(
    r"大模型|LLM|AIGC|AI(?:产品|平台|工具|系统|能力|功能|方向|策略|驱动|赋能|落地|体验)|"
    r"GPT|ChatGPT|Copilot|RAG|向量数据库|智能体|Agent(?:产品|平台)|"
    r"智能客服|AI客服|自动驾驶|智驾|座舱AI|AI Agent", re.I
)
# 手动排除：AI_STRONG 误匹配（AI 是工具而非产品本身）
STAGE3_MANUAL_EXCL: set[str] = {
    "JR2026041300Z",  # 数据产品：AI工具=「利用AI工具提升分析效率」，非AI PM
}
# AI_WEAK 中手动纳入的岗位（AI 信号虽弱但确实与 AI 相关）
STAGE3_KEEP_IDS: set[str] = {
    "JR20260511006",  # 安全准入：协同AI在风险识别、安全核验场景落地
    "JR20260227008",  # 内容安全：推动内容安全智能化
    "JR2026010600A",  # 平台产品：推荐算法策略升级
    "JR2026012000S",  # 安全产品：负责安全领域人工智能产品全流程设计
    "JR2026032600V",  # 用增中台创意方向：搭建 AI 驱动的素材生产体系
}

# ── 工具函数 ────────────────────────────────────────────────
def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def has_ai(text: str) -> bool:
    return bool(AI_KW.search(text))


# ── Step 1: 列表抓取（Playwright 翻页）─────────────────────
def fetch_list() -> list[dict]:
    all_items: list[dict] = []
    total = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page_num = 1

        while True:
            captured: dict = {}

            def on_resp(resp, _cap=captured):
                if "front/list" in resp.url and resp.status == 200:
                    try:
                        d = json.loads(resp.text())
                        if (d.get("meta") or {}).get("code") == 0:
                            _cap["data"] = d.get("data", {})
                    except:
                        pass

            page.on("response", on_resp)
            page.goto(
                f"https://talent.didiglobal.com/social/list/1?jobType=3&page={page_num}",
                wait_until="networkidle", timeout=50000
            )
            time.sleep(1)
            page.remove_listener("response", on_resp)

            if "data" not in captured:
                print(f"  [List] page={page_num} 未捕获，停止", flush=True)
                break

            d = captured["data"]
            items = d.get("items", [])
            if total is None:
                total = d.get("total", 0)
            all_items.extend(items)
            print(f"  [List] page={page_num}  累计={len(all_items)}/{total}", flush=True)

            if len(all_items) >= total or not items:
                break
            page_num += 1

        browser.close()

    return all_items


# ── Step 2: 详情抓取（urllib）──────────────────────────────
def fetch_detail(jd_id: int):
    url = DETAIL_TMPL.format(jd_id)
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        if (d.get("meta") or {}).get("code") == 0:
            return d.get("data")
    except Exception as e:
        print(f"  [Detail] {jd_id} 失败: {e}", flush=True)
    return None


# ── Step 3: 结构化 ─────────────────────────────────────────
def structurize(raw: list[dict]) -> list[dict]:
    jobs = []
    for item in raw:
        jd_id = item.get("jdId") or item.get("id")
        jd_no = item.get("jdNo", "")
        title = clean(item.get("jobName", ""))
        dept  = clean(item.get("deptName", ""))
        job_desc = clean(item.get("jobDesc", ""))
        qualification = clean(item.get("qualification", ""))
        jd   = f"{job_desc}\n{qualification}".strip()
        url  = JOB_TMPL.format(jd_id)
        jobs.append({
            "id":    jd_no or str(jd_id),
            "jdId":  jd_id,
            "title": title,
            "dept":  dept,
            "area":  item.get("workArea", ""),
            "jd":    jd,
            "url":   url,
        })
    return jobs


# ── Stage1 ─────────────────────────────────────────────────
def stage1(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    ok, ex = [], []
    for j in jobs:
        text = j["title"] + " " + j["jd"][:500]
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
def stage2(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
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
def stage3(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    ok, ex = [], []
    for j in jobs:
        text = j["title"] + "\n" + j["jd"]
        if j["id"] in STAGE3_MANUAL_EXCL:
            j["excl_reason"] = "Stage3:manual_excl"
            ex.append(j)
        elif AI_STRONG.search(text) or j["id"] in STAGE3_KEEP_IDS:
            ok.append(j)
        else:
            j["excl_reason"] = "Stage3:no_ai_signal"
            ex.append(j)
    return ok, ex


# ── URL 验证 ────────────────────────────────────────────────
def verify_urls(jobs: list[dict]):
    # DiDi 是 SPA，detail API 返回 200 即视为可访问
    def check(j):
        url = DETAIL_TMPL.format(j["jdId"])
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            return j["id"], (d.get("meta") or {}).get("code") == 0
        except:
            return j["id"], False

    print("\n[URLVerify] 验证中...", flush=True)
    with ThreadPoolExecutor(max_workers=8) as exe:
        futures = {exe.submit(check, j): j for j in jobs}
        bad = []
        for f in as_completed(futures):
            jid, ok = f.result()
            if not ok:
                bad.append(jid)
    if bad:
        print(f"  ⚠️  不可访问: {bad}", flush=True)
    else:
        print(f"  ✓  全部 {len(jobs)} 条可访问", flush=True)


# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print("[任务16] 滴滴社招 AI PM 岗位", flush=True)

    # Step 1: 获取列表
    if "--reuse-raw" in sys.argv and os.path.exists(RAW_FILE):
        print(f"[Step1] 复用 {RAW_FILE}", flush=True)
        raw_items = json.load(open(RAW_FILE))
    else:
        print("[Step1] Playwright 翻页抓取列表...", flush=True)
        raw_items = fetch_list()
        print(f"[Step1] 列表共 {len(raw_items)} 条", flush=True)

        # Step 2: 补全详情（jobDesc/qualification 来自 detail API）
        print("[Step2] urllib 并发抓取详情...", flush=True)
        with ThreadPoolExecutor(max_workers=10) as exe:
            futures = {exe.submit(fetch_detail, item.get("jdId")): item for item in raw_items}
            for f in as_completed(futures):
                item = futures[f]
                detail = f.result()
                if detail:
                    item["jobDesc"]      = detail.get("jobDesc", "")
                    item["qualification"] = detail.get("qualification", "")
        print(f"[Step2] 详情补全完成", flush=True)

        json.dump(raw_items, open(RAW_FILE, "w"), ensure_ascii=False, indent=2)
        print(f"[Step2] raw → {RAW_FILE}", flush=True)

    jobs = structurize(raw_items)
    print(f"[Step3] 结构化 {len(jobs)} 条", flush=True)

    s1_ok, s1_ex = stage1(jobs)
    print(f"[Stage1] 保留 {len(s1_ok)}  排除 {len(s1_ex)}", flush=True)
    for j in s1_ex:
        print(f"  ✗ [{j['id']}] {j['title']}  ({j.get('excl_reason','')})", flush=True)

    s2_ok, s2_ex = stage2(s1_ok)
    print(f"[Stage2] 保留 {len(s2_ok)}  排除 {len(s2_ex)}", flush=True)
    for j in s2_ex:
        print(f"  ✗ [{j['id']}] {j['title']}  ({j.get('excl_reason','')})", flush=True)

    final, s3_ex = stage3(s2_ok)
    verify_urls(final)

    json.dump(final,               open(OUT_FILE,  "w"), ensure_ascii=False, indent=2)
    json.dump(s1_ex+s2_ex+s3_ex,  open(EXCL_FILE, "w"), ensure_ascii=False, indent=2)
    print(f"\n[Done] 保留 {len(final)}  排除 {len(s1_ex+s2_ex+s3_ex)}", flush=True)
    print(f"  → {OUT_FILE}", flush=True)

    print("\n[待Stage3复核]", flush=True)
    for j in final:
        flag = "★AI" if has_ai(j["title"] + j["jd"][:300]) else "   "
        print(f"  {flag} [{j['id']}] {j['title']}  [{j['dept']}]", flush=True)


if __name__ == "__main__":
    main()
