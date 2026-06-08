#!/usr/bin/env python3
"""
任务31/32/33：B站 社招+校招+实习 AI PM 岗位抓取
运行：python3 /Users/Shared/bilibili_scrape.py
"""
from playwright.sync_api import sync_playwright
import json, re, os, time
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
SAVE_DIR = get_jobs_dir()
os.makedirs(SAVE_DIR, exist_ok=True)

AI_KW = re.compile(r'AI|人工智能|大模型|LLM|智能|AIGC|GPT|NLP|Agent|多模态|Prompt', re.I)
PM_KEEP = re.compile(r'产品经理|产品总监|产品专家|产品负责人|PM\b|Product Manager', re.I)
TECH_SIG = re.compile(r'预训练|RLHF|SFT|模型蒸馏|Fine.tuning|算力调度|GPU资源|CUDA|推理优化|MLOps|IaaS|PaaS|底层架构|分布式训练|模型压缩')
PROD_SIG = re.compile(r'产品规划|用户体验|业务场景|商业化|路线图|产品迭代|需求分析|用户调研|落地|产品化|产品设计|用户需求|产品策略|场景|需求')

TASKS = [
    {
        "name": "bilibili_social", "company": "B站", "type": "社招",
        "url": "https://jobs.bilibili.com/social/positions?name=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&type=3",
        "api": "https://jobs.bilibili.com/api/srs/position/positionList",
        "payload_base": {
            "positionName": "产品经理", "postCode": [], "postCodeList": [],
            "workLocationList": [], "workTypeList": ["3"], "positionTypeList": ["3"],
            "deptCodeList": [], "recruitType": 0, "practiceTypes": [], "onlyHotRecruit": 0,
        },
    },
    {
        "name": "bilibili_campus", "company": "B站", "type": "校招",
        "url": "https://jobs.bilibili.com/campus/positions?name=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&type=0",
        "api": "https://jobs.bilibili.com/api/campus/position/positionList",
        "payload_base": {
            "positionName": "产品经理", "postCode": [], "postCodeList": [],
            "workLocationList": [], "workTypeList": ["0"], "positionTypeList": ["0"],
            "deptCodeList": [], "recruitType": None, "practiceTypes": [], "onlyHotRecruit": 0,
        },
    },
    {
        "name": "bilibili_intern", "company": "B站", "type": "实习",
        "url": "https://jobs.bilibili.com/campus/positions?name=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&type=3",
        "api": "https://jobs.bilibili.com/api/campus/position/positionList",
        "payload_base": {
            "positionName": "产品经理", "postCode": [], "postCodeList": [],
            "workLocationList": [], "workTypeList": ["3"], "positionTypeList": ["3"],
            "deptCodeList": [], "recruitType": None, "practiceTypes": [], "onlyHotRecruit": 0,
        },
    },
]


def extract_kw(text):
    kw_patterns = ['大模型', 'LLM', 'Agent', 'AIGC', '多模态', '智能', '产品规划', '用户体验', '需求分析', 'NLP', 'Prompt', 'AI']
    found = [k for k in kw_patterns if k.lower() in text.lower()]
    return found[:6] if found else ['AI产品']


def get_cats(name, jd, job_type):
    if re.search(r'大模型|LLM|Agent|AIGC|多模态|Prompt', jd, re.I):
        ai_type = 'AI原生'
    elif re.search(r'AI|智能|人工智能', jd, re.I):
        ai_type = 'AI赋能'
    else:
        ai_type = 'AI赋能'
    bu_match = re.search(r'[-—](.+?)[-—]', name)
    bu = bu_match.group(1).strip() if bu_match else 'B站'
    if job_type == '实习':
        level = '实习生'
    elif re.search(r'高级|资深|Senior|高阶', name, re.I):
        level = '高级'
    else:
        level = '中级'
    return [ai_type, bu, level]


def normalize_raw(j):
    """统一 srs/campus positionList 字段到旧格式。"""
    name = j.get("positionName") or j.get("name") or j.get("title") or ""
    desc = j.get("positionDescription") or j.get("desc") or j.get("description") or ""
    req = j.get("positionRequirement") or j.get("requirement") or ""
    loc = j.get("workLocationName") or j.get("city") or ""
    if isinstance(j.get("workLocationList"), list):
        loc = ",".join(str(x) for x in j["workLocationList"])
    return {
        "id": j.get("id"),
        "name": name,
        "desc": desc,
        "requirement": req,
        "location": loc,
        "city": loc,
    }


def fetch_position_list(ctx, page, task):
    """打开页面捕获鉴权头，再分页拉 positionList。"""
    captured = {"headers": None, "post": None}

    def on_req(req):
        if req.method == "POST" and "positionList" in req.url:
            captured["headers"] = dict(req.headers)
            captured["post"] = req.post_data

    page.on("request", on_req)
    page.goto(task["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    if not captured["headers"]:
        raise RuntimeError(f"未捕获 positionList 请求: {task['name']}")

    hdrs = captured["headers"]
    all_items = []
    page_num = 1
    page_size = 20
    total_pages = 1

    while page_num <= total_pages and page_num <= 50:
        payload = dict(task["payload_base"])
        payload.update({"pageSize": page_size, "pageNum": page_num})
        resp = ctx.request.post(task["api"], headers=hdrs, data=json.dumps(payload))
        if resp.status != 200:
            print(f"  page{page_num} HTTP {resp.status}", flush=True)
            break
        data = resp.json()
        if data.get("code") not in (0, None):
            print(f"  page{page_num} API code={data.get('code')} msg={data.get('message')}", flush=True)
            break
        block = data.get("data") or {}
        items = block.get("list") or []
        total = block.get("total") or block.get("count") or 0
        if page_num == 1:
            total_pages = max((total + page_size - 1) // page_size, 1) if total else 1
            print(f"  total={total} pages={total_pages}", flush=True)
        if not items:
            break
        all_items.extend(items)
        print(f"  page{page_num}: +{len(items)}", flush=True)
        if len(items) < page_size:
            break
        page_num += 1
        time.sleep(0.8)

    return [normalize_raw(j) for j in all_items]


def process_jobs(raw_jobs, company, job_type, base_url_prefix):
    passed, excl = [], []
    for j in raw_jobs:
        name = re.sub(r'\s+', ' ', (j.get("name") or j.get("title", ""))).strip()
        jid = str(j.get("id") or j.get("job_id", ""))
        desc = j.get("desc") or j.get("description", "") or ""
        req = j.get("requirement", "") or ""
        jd = re.sub(r'\s+', ' ', desc + " " + req).strip()

        has_ai = bool(AI_KW.search(name + " " + jd[:600]))
        tech_s = len(TECH_SIG.findall(jd))
        prod_s = len(PROD_SIG.findall(jd))

        cats = get_cats(name, jd, job_type)
        summary_raw = re.sub(r'\s+', ' ', desc[:300]).strip()
        first_sent = re.split(r'[。；\n]', summary_raw)[0].strip()
        summary = first_sent[:80] if first_sent else name
        keywords = extract_kw(name + " " + jd)

        loc = ""
        if isinstance(j.get("location"), list):
            loc = ",".join(str(l) for l in j.get("location", []))
        elif j.get("city"):
            loc = j["city"]

        url = f"https://jobs.bilibili.com/detail?id={jid}"

        item = {
            "id": jid, "company": company, "bu": cats[1],
            "title": name, "city": loc, "type": job_type,
            "url": url, "jd": jd, "summary": summary,
            "keywords": keywords, "cats": cats,
            "has_ai": has_ai, "tech_s": tech_s, "prod_s": prod_s
        }

        if not PM_KEEP.search(name):
            item["excl_reason"] = "S1:no_pm"; excl.append(item); continue
        if not has_ai:
            item["excl_reason"] = "S3:no_ai"; excl.append(item); continue
        if tech_s >= 7:
            item["excl_reason"] = f"S2:tech≥7"; excl.append(item); continue
        if tech_s >= 5 and prod_s <= 1:
            item["excl_reason"] = f"S2:tech≥5,prod≤1"; excl.append(item); continue

        passed.append(item)
        print(f"  ✅ {name}", flush=True)
    return passed, excl


all_results = {}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])

    for task in TASKS:
        print(f"\n{'='*50}", flush=True)
        print(f"[{task['name']}] Fetching {task['type']}...", flush=True)

        ctx = browser.new_context(
            user_agent=UA,
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"}
        )
        page = ctx.new_page()

        try:
            captured = fetch_position_list(ctx, page, task)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            captured = []

        raw_path = f"{SAVE_DIR}/{task['name']}_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(captured, f, ensure_ascii=False, indent=2)
        print(f"  Raw saved: {len(captured)} jobs → {raw_path}", flush=True)

        passed, excl = process_jobs(captured, task['company'], task['type'], "")
        all_results[task['name']] = {'passed': passed, 'excl': excl}

        with open(f"{SAVE_DIR}/{task['name']}_jobs.json", "w", encoding="utf-8") as f:
            json.dump(passed, f, ensure_ascii=False, indent=2)
        with open(f"{SAVE_DIR}/{task['name']}_excluded.json", "w", encoding="utf-8") as f:
            json.dump(excl, f, ensure_ascii=False, indent=2)

        print(f"  ✅ {task['name']}: {len(passed)} AI PM | {len(excl)} excluded", flush=True)
        ctx.close()

    browser.close()

print("\n\n=== 汇总 ===", flush=True)
for name, res in all_results.items():
    print(f"{name}: {len(res['passed'])} AI PM 岗位", flush=True)
