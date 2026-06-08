#!/usr/bin/env python3
"""
任务30：小米实习 AI PM 岗位抓取
运行：python3 /Users/Shared/xiaomi_intern_scrape.py
"""
from playwright.sync_api import sync_playwright
import json, re, os, time
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
SAVE_DIR = get_jobs_dir()
BASE = "https://xiaomi.jobs.f.mioffice.cn"
FUNC_CAT = "7178035552473448557"  # 产品类

AI_KW = re.compile(r'AI|人工智能|大模型|LLM|智能|AIGC|GPT|NLP|Agent|多模态|Prompt', re.I)
PM_KEEP = re.compile(r'产品经理|产品总监|产品专家|产品负责人|PM\b', re.I)

TECH_SIG = re.compile(r'预训练|RLHF|SFT|模型蒸馏|Fine.tuning|算力调度|GPU资源|CUDA|推理优化|MLOps|IaaS|PaaS|底层架构|分布式训练|模型压缩|代码能力|编程能力|基础设施')
PROD_SIG = re.compile(r'产品规划|用户体验|业务场景|商业化|路线图|产品迭代|需求分析|用户调研|Prompt Engineering|Agent应用|落地|产品化|产品设计|用户需求|产品策略|产品方案|场景|需求')

all_raw = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(user_agent=UA, extra_http_headers={"Accept-Language":"zh-CN,zh;q=0.9"})
    page = ctx.new_page()

    for pg in range(1, 20):  # max 20 pages
        url = f"{BASE}/internship/?keywords=&functionCategory={FUNC_CAT}&current={pg}&limit=10"
        print(f"[pg{pg}] {url}", flush=True)
        prev_count = len(all_raw)
        try:
            with page.expect_response(
                lambda r: "search/job/posts" in r.url and r.status == 200,
                timeout=25000,
            ) as resp_info:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
            resp = resp_info.value
            d = resp.json()
            lst = d.get("data", {}).get("job_post_list", [])
            total = d.get("data", {}).get("count", 0)
            all_raw.extend(lst)
            print(f"  captured +{len(lst)} total={total} cumul={len(all_raw)}", flush=True)
        except Exception as e:
            print(f"  page err: {e}", flush=True)
            if pg > 1:
                break
        time.sleep(1.5)
        if len(all_raw) == prev_count and pg > 1:
            print(f"  no new data at pg{pg}, stopping", flush=True)
            break

    browser.close()

# Dedup
seen = set()
deduped = [j for j in all_raw if not (j.get("id") in seen or seen.add(j.get("id")))]
print(f"\nDeduped raw: {len(deduped)}", flush=True)
os.makedirs(SAVE_DIR, exist_ok=True)
with open(f"{SAVE_DIR}/xiaomi_intern_raw.json","w",encoding="utf-8") as f:
    json.dump(deduped, f, ensure_ascii=False, indent=2)

# Stage1 + Stage2 + Stage3
AI_STRONG = re.compile(r'AI产品|大模型|LLM|AIGC|Agent|人工智能|多模态|智能助手|NLP|Prompt', re.I)

def extract_kw(text):
    kw_patterns = ['大模型','LLM','Agent','AIGC','多模态','智能','产品规划','用户体验','需求分析','NLP','Prompt']
    found = [k for k in kw_patterns if k.lower() in text.lower()]
    return found[:6] if found else ['AI产品']

def get_cats(name, jd):
    # Layer1: AI type
    if re.search(r'大模型|LLM|Agent|AIGC|多模态|Prompt', jd, re.I):
        ai_type = 'AI原生'
    elif re.search(r'AI|智能|人工智能', jd, re.I):
        ai_type = 'AI赋能'
    else:
        ai_type = 'AI赋能'
    # Layer2: BU from title
    bu_match = re.search(r'[-—](.+?)[-—]', name)
    bu = bu_match.group(1) if bu_match else '小米'
    # Layer3: level
    if re.search(r'实习|intern', name, re.I):
        level = '实习生'
    elif re.search(r'高级|资深|Senior|高阶', name, re.I):
        level = '高级'
    else:
        level = '中级'
    return [ai_type, bu, level]

passed, excl = [], []
for idx, j in enumerate(deduped, 1):
    name = re.sub(r'\s+', ' ', j.get("title","")).strip()
    jid = str(j.get("id",""))
    desc = j.get("description","") or ""
    req = j.get("requirement","") or ""
    jd = re.sub(r'\s+', ' ', desc + " " + req).strip()

    has_ai = bool(AI_KW.search(name + " " + jd[:600]))
    tech_s = len(TECH_SIG.findall(jd))
    prod_s = len(PROD_SIG.findall(jd))

    url = f"{BASE}/position-detail/?id={jid}"
    summary_raw = re.sub(r'\s+', ' ', desc[:300]).strip()
    first_sent = re.split(r'[。；\n]', summary_raw)[0].strip()
    summary = first_sent[:80] if first_sent else name
    keywords = extract_kw(name + " " + jd)
    cats = get_cats(name, jd)

    item = {
        "id": jid, "company": "小米", "bu": cats[1],
        "title": name, "city": j.get("location_name",""),
        "type": "实习", "url": url,
        "jd": jd, "summary": summary, "keywords": keywords,
        "cats": cats,
        "has_ai": has_ai, "tech_s": tech_s, "prod_s": prod_s
    }

    if not PM_KEEP.search(name):
        item["excl_reason"] = "S1:no_pm"; excl.append(item); continue
    if not has_ai:
        item["excl_reason"] = "S3:no_ai"; excl.append(item); continue
    if tech_s >= 7:
        item["excl_reason"] = f"S2:tech≥7({tech_s})"; excl.append(item); continue
    if tech_s >= 5 and prod_s <= 1:
        item["excl_reason"] = f"S2:tech≥5,prod≤1"; excl.append(item); continue

    passed.append(item)
    print(f"  ✅ {name}", flush=True)

print(f"\n{'='*50}", flush=True)
print(f"✅ AI PM 实习: {len(passed)} 条 | 排除: {len(excl)} 条", flush=True)

with open(f"{SAVE_DIR}/xiaomi_intern_jobs.json","w",encoding="utf-8") as f:
    json.dump(passed, f, ensure_ascii=False, indent=2)
with open(f"{SAVE_DIR}/xiaomi_intern_excluded.json","w",encoding="utf-8") as f:
    json.dump(excl, f, ensure_ascii=False, indent=2)
print(f"Saved to {SAVE_DIR}/", flush=True)
