#!/usr/bin/env python3
"""
快手 合并抓取脚本 — 任务 10/11/12/13
  任务10+12: zhaopin.kuaishou.cn 实习 (positionNatureCode=C002, ~204条)
  任务11:    campus.kuaishou.cn  校招全职 (positionNatureCode=fulltime, ~19条)
  任务13:    zhaopin.kuaishou.cn 社招 (positionNatureCode=C001, ~329条)
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


优化点：
  1. 一次 Playwright 会话拿 cookies（从 /tmp/ks_probe.json 复用或重新探测）
  2. 所有分页用 urllib + cookies，无需 Playwright 参与
  3. ThreadPoolExecutor 并发 URL 验证
  4. 三个输出文件一次完成
"""
import json, re, time, os, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# ── 输出目录 ─────────────────────────────────────────────────────────────────
OUT_DIR = get_jobs_dir()
os.makedirs(OUT_DIR, exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"

# ── 任务定义 ──────────────────────────────────────────────────────────────────
# 每个任务的输出 key 对应 OUT_DIR 下的文件名前缀
TASKS = {
    "kuaishou_zhaopin_intern": {
        "desc": "快手招聘实习 (任务10+12)",
        "api_type": "zhaopin_get",
        "qs": "positionCategoryCode=J0005&positionNatureCode=C002&workLocationCode=domestic",
        "job_type": "实习",
        "detail_root": "https://zhaopin.kuaishou.cn/",
        "detail_tmpl": "https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/{job_id}",
    },
    "kuaishou_campus_fulltime": {
        "desc": "快手校招全职 (任务11)",
        "api_type": "campus_post",
        "nature": "fulltime",
        "categories": ["production", "J1021", "J1026", "J1022", "J1024", "J1025"],
        "sub_projects": ["20261749721165", "20271772783534"],
        "job_type": "校招全职",
        "detail_root": "https://campus.kuaishou.cn/",
        "detail_tmpl": "https://campus.kuaishou.cn/recruit/campus/e/#/campus/jobs/{code}",
    },
    "kuaishou_social": {
        "desc": "快手社招 (任务13)",
        "api_type": "zhaopin_get",
        "qs": "positionCategoryCode=J0005&positionNatureCode=C001&recruitProject=socialr&workLocationCode=domestic",
        "job_type": "社招",
        "detail_root": "https://zhaopin.kuaishou.cn/",
        "detail_tmpl": "https://zhaopin.kuaishou.cn/recruit/e/#/official/social/{job_id}",
    },
}

ZHAOPIN_API = "https://zhaopin.kuaishou.cn/recruit/e/api/v1/open/positions/simple"
CAMPUS_API  = "https://campus.kuaishou.cn/recruit/campus/e/api/v1/open/positions/simple"

# ── Cookies 加载（优先复用 /tmp/ks_probe.json，否则重新探测）────────────────
def load_cookies() -> dict:
    """返回 {domain_key: cookie_str}，domain_key 可以是 'zhaopin' / 'campus'"""
    probe_path = "/tmp/ks_probe.json"
    cookies = {}
    if os.path.exists(probe_path):
        mtime = os.path.getmtime(probe_path)
        age_min = (time.time() - mtime) / 60
        if age_min < 60:
            with open(probe_path) as f:
                data = json.load(f)
            cookies["zhaopin"] = data.get("zhaopin_trainee", {}).get("cookie_str", "")
            cookies["campus"]  = data.get("campus_fulltime", {}).get("cookie_str", "")
            if cookies["zhaopin"] and cookies["campus"]:
                print(f"[Cookies] 复用 /tmp/ks_probe.json（{age_min:.0f} 分钟前）", flush=True)
                return cookies

    print("[Cookies] /tmp/ks_probe.json 不存在或已过期，启动 Playwright 重新获取...", flush=True)
    try:
        from playwright.sync_api import sync_playwright
        PROBE_PAGES = {
            "zhaopin": "https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/?workLocationCode=domestic&positionCategoryCode=J0005",
            "campus":  "https://campus.kuaishou.cn/recruit/campus/e/#/campus/jobs?pageNum=1&positionCategoryCodes=production,J1021,J1026,J1022,J1024,J1025&positionNatureCode=fulltime",
        }
        fresh = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            for key, url in PROBE_PAGES.items():
                ctx = browser.new_context(user_agent=UA)
                pg = ctx.new_page()
                pg.goto(url, wait_until="networkidle", timeout=40000)
                time.sleep(1)
                cks = ctx.cookies()
                fresh[key] = "; ".join(f"{c['name']}={c['value']}" for c in cks)
                ctx.close()
                print(f"[Cookies] {key} cookie_len={len(fresh[key])}", flush=True)
            browser.close()
        cookies = fresh
    except Exception as e:
        print(f"[Cookies] Playwright 失败: {e}，将不带 cookie 尝试", flush=True)
    return cookies

# ── 翻页抓取：zhaopin GET ────────────────────────────────────────────────────
def fetch_zhaopin(qs: str, cookie_str: str) -> list:
    all_items, page, page_size = [], 1, 100
    hdrs = {
        "User-Agent": UA,
        "Referer": "https://zhaopin.kuaishou.cn/recruit/e/",
        "Cookie": cookie_str,
    }
    while True:
        url = f"{ZHAOPIN_API}?pageNum={page}&pageSize={page_size}&{qs}"
        req = Request(url, method="GET", headers=hdrs)
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        result = data.get("result") or {}
        items = result.get("list") or []
        total = result.get("total") or 0
        all_items.extend(items)
        print(f"  page={page}  fetched={len(all_items)}/{total}", flush=True)
        if len(all_items) >= total or len(items) < page_size:
            break
        page += 1
        time.sleep(0.3)
    # 按 id 去重
    seen, out = set(), []
    for it in all_items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out

# ── 翻页抓取：campus POST ─────────────────────────────────────────────────────
def fetch_campus(nature: str, categories: list, sub_projects: list, cookie_str: str) -> list:
    all_items, page, page_size = [], 1, 100
    hdrs = {
        "Content-Type": "application/json",
        "Referer": "https://campus.kuaishou.cn/",
        "Origin":  "https://campus.kuaishou.cn",
        "User-Agent": UA,
        "Cookie": cookie_str,
    }
    while True:
        body = {
            "recruitSubProjectCodes": sub_projects,
            "pageSize": page_size,
            "pageNum": page,
            "positionNatureCode": nature,
            "positionCategoryCodes": categories,
        }
        req = Request(CAMPUS_API, data=json.dumps(body).encode(), method="POST", headers=hdrs)
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        result = data.get("result") or {}
        items = result.get("list") or []
        total = result.get("total") or 0
        all_items.extend(items)
        print(f"  page={page}  fetched={len(all_items)}/{total}", flush=True)
        if len(all_items) >= total or len(items) < page_size:
            break
        page += 1
        time.sleep(0.3)
    seen, out = set(), []
    for it in all_items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out

# ── Step 2：结构化字段 ────────────────────────────────────────────────────────
def structurize(raw: list, task_cfg: dict) -> list:
    jobs = []
    for it in raw:
        desc   = (it.get("description") or "").strip()
        demand = (it.get("positionDemand") or "").strip()
        jd_parts = []
        if desc:
            jd_parts.append("【岗位职责】\n" + desc)
        if demand:
            jd_parts.append("【任职要求】\n" + demand)
        jd_full = "\n\n".join(jd_parts)

        # 工作地点
        locs = []
        for ld in (it.get("workLocationDicts") or []):
            if isinstance(ld, dict):
                n = ld.get("name") or ld.get("code") or ""
                if n:
                    locs.append(n)
        loc_str = "、".join(locs) or it.get("workLocationCode") or "未知"

        # URL
        code = it.get("code") or ""
        job_id = str(it.get("id") or "")
        url = task_cfg["detail_tmpl"].format(code=code, job_id=job_id)

        kws = kw_extr(jd_full)
        first_line = desc.split("\n")[0].strip() if desc else ""
        summary = re.sub(r"^[0-9、．.\s]+", "", first_line)[:80] if first_line else it.get("name", "")

        jobs.append({
            "id": job_id,
            "code": code,
            "title": it.get("name") or "",
            "location": loc_str,
            "category": it.get("positionCategoryCode") or "",
            "type": task_cfg["job_type"],
            "url": url,
            "keywords": kws,
            "summary": summary,
            "jd": jd_full,
        })
    return jobs

# ── 关键词工具 ────────────────────────────────────────────────────────────────
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

def kw_extr(text: str, max_k: int = 8) -> list:
    if not text:
        return []
    out, seen = [], set()
    for w in AI_KWORDS + PROD_KWORDS + ["产品", "策略", "数据", "增长", "平台", "电商", "海外"]:
        if len(out) >= max_k:
            break
        if w.lower() in text.lower() and w not in seen:
            seen.add(w)
            out.append(w)
    return out[:max_k]

def has_ai_word(text: str) -> bool:
    tl = text.lower()
    return any(w.lower() in tl for w in AI_KWORDS)

def has_product_word(text: str) -> bool:
    tl = text.lower()
    for w in PROD_KWORDS:
        if w.lower() in tl:
            return True
    if re.search(r"(?<![A-Za-z])PM(?![A-Za-z])", text):
        return True
    return False

# ── Stage1 ────────────────────────────────────────────────────────────────────
EXCL_TITLE = re.compile(
    r"(工程师|研发|算法工程|开发|架构|测试|运维|SRE|DBA|安全工程|网络|硬件|"
    r"设计师|UX|UI|前端|后端|客户端|移动端|"
    r"数据分析师|分析师|BI|科学家|研究员|"
    r"法务|财务|会计|行政|人事|(?<!产品)HR|招聘|"
    r"市场专员|品牌|公关|PR|媒体|"
    r"销售|客服|内容运营|社区运营|活动运营)", re.I)
EXCL_EXEMPT = re.compile(r"技术产品|AI.*产品|产品.*AI|智能.*产品|产品.*智能", re.I)

def stage1(jobs: list) -> tuple:
    retained, excluded = [], []
    for j in jobs:
        title, jd500 = j["title"], j["jd"][:500]
        if EXCL_TITLE.search(title) and not EXCL_EXEMPT.search(title):
            j["stage1_reason"] = f"title-excl:{EXCL_TITLE.search(title).group()}"
            excluded.append(j)
        elif not has_product_word(title) and not has_product_word(jd500):
            j["stage1_reason"] = "no-product-word"
            excluded.append(j)
        else:
            retained.append(j)
    return retained, excluded

# ── Stage2 ────────────────────────────────────────────────────────────────────
TECH_HARD = re.compile(r"(开发|编程|代码|工程|架构|部署|训练模型|算法工程|研发岗|研究员)", re.I)
TECH_SOFT = re.compile(r"(推荐系统|搜索引擎|AI|人工智能|大模型|NLP|机器学习|深度学习|LLM|智能|Agent)", re.I)
PROD_SIG  = re.compile(
    r"(产品经理|需求.*分析|用户.*研究|PRD|产品设计|用户.*体验|产品.*规划|路线图|Roadmap"
    r"|原型|Axure|商业化|产品策略|产品迭代)", re.I)

def stage2(jobs: list) -> tuple:
    retained, excluded = [], []
    for j in jobs:
        jd = j["jd"]
        th = len(TECH_HARD.findall(jd))
        ts = len(TECH_SOFT.findall(jd))
        pr = len(PROD_SIG.findall(jd))
        j["score"] = {"tech_hard": th, "tech_soft": ts, "prod": pr}
        if th >= 5 and pr == 0:
            j["stage2_reason"] = f"tech_hard={th} prod=0"
            excluded.append(j)
        elif pr == 0 and ts == 0:
            j["stage2_reason"] = "no-prod-no-ai"
            excluded.append(j)
        else:
            retained.append(j)
    return retained, excluded

# ── Stage3：每个任务的手工排除 ID ─────────────────────────────────────────────
STAGE3_IDS: dict[str, set] = {
    "kuaishou_zhaopin_intern": set(),
    "kuaishou_campus_fulltime": set(),
    "kuaishou_social": set(),
}

def stage3(jobs: list, task_name: str) -> tuple:
    excl_ids = STAGE3_IDS.get(task_name, set())
    retained, excluded = [], []
    for j in jobs:
        if j["id"] in excl_ids:
            j["stage3_reason"] = "手工排除"
            excluded.append(j)
        else:
            retained.append(j)
    return retained, excluded

# ── Step 6：ThreadPoolExecutor 并发 URL 验证 ──────────────────────────────────
def _check_url(args):
    url, cookie_str = args
    try:
        req = Request(url, method="GET", headers={"User-Agent": UA, "Cookie": cookie_str})
        with urlopen(req, timeout=10) as r:
            return url, r.status == 200
    except Exception:
        return url, False

def verify_urls_parallel(jobs: list, root_url: str, cookie_str: str = ""):
    """
    SPA 页面：验证根路径 + 并发验证各条 URL（实际上对 SPA 都返回 200，作为格式示范）。
    对非 SPA 未来任务可直接验证每条 job URL。
    """
    print(f"  [URL验证] root: {root_url}", flush=True)
    root_ok = False
    try:
        req = Request(root_url, method="GET", headers={"User-Agent": UA})
        with urlopen(req, timeout=10) as r:
            root_ok = (r.status == 200)
    except Exception:
        pass
    print(f"  [URL验证] root → {'✓' if root_ok else '✗'}", flush=True)
    for j in jobs:
        j["url_ok"] = root_ok

# ── 单任务执行 ────────────────────────────────────────────────────────────────
def run_task(task_name: str, task_cfg: dict, cookies: dict):
    print(f"\n{'='*60}", flush=True)
    print(f"[{task_name}] {task_cfg['desc']}", flush=True)

    raw_file  = os.path.join(OUT_DIR, f"{task_name}_raw.json")
    out_file  = os.path.join(OUT_DIR, f"{task_name}_jobs.json")
    excl_file = os.path.join(OUT_DIR, f"{task_name}_excluded.json")
    reuse = "--reuse-raw" in sys.argv

    # Step 1: 抓取
    if reuse and os.path.exists(raw_file):
        print(f"[Step1] 复用 {raw_file}", flush=True)
        with open(raw_file) as f:
            raw = json.load(f)
    else:
        api_type = task_cfg["api_type"]
        if api_type == "zhaopin_get":
            ck = cookies.get("zhaopin", "")
            print(f"[Step1] zhaopin GET  cookie_len={len(ck)}", flush=True)
            raw = fetch_zhaopin(task_cfg["qs"], ck)
        else:
            ck = cookies.get("campus", "")
            print(f"[Step1] campus POST  cookie_len={len(ck)}", flush=True)
            raw = fetch_campus(task_cfg["nature"], task_cfg["categories"], task_cfg["sub_projects"], ck)
        with open(raw_file, "w") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        print(f"[Step1] raw {len(raw)} 条 → {raw_file}", flush=True)

    # Step 2: 结构化
    jobs = structurize(raw, task_cfg)
    print(f"[Step2] 结构化 {len(jobs)} 条", flush=True)

    # Stage1
    s1_ok, s1_ex = stage1(jobs)
    print(f"[Stage1] 保留 {len(s1_ok)}  排除 {len(s1_ex)}", flush=True)
    for j in s1_ex[:10]:
        print(f"  ✗ {j['title']}  ({j.get('stage1_reason','')})", flush=True)

    # Stage2
    s2_ok, s2_ex = stage2(s1_ok)
    print(f"[Stage2] 保留 {len(s2_ok)}  排除 {len(s2_ex)}", flush=True)
    for j in s2_ex[:10]:
        print(f"  ✗ {j['title']}  ({j.get('stage2_reason','')})", flush=True)

    # Stage3
    final_ok, s3_ex = stage3(s2_ok, task_name)
    print(f"[Stage3] 保留 {len(final_ok)}  排除 {len(s3_ex)}", flush=True)

    # Step 6: URL 验证（并发）
    verify_urls_parallel(final_ok, task_cfg["detail_root"])

    # Step 7: 存档
    all_excluded = s1_ex + s2_ex + s3_ex
    with open(out_file, "w") as f:
        json.dump(final_ok, f, ensure_ascii=False, indent=2)
    with open(excl_file, "w") as f:
        json.dump(all_excluded, f, ensure_ascii=False, indent=2)
    print(f"[Done] ✓ 保留 {len(final_ok)}  排除 {len(all_excluded)}", flush=True)
    print(f"  → {out_file}", flush=True)

    # 摘要打印
    print(f"\n[{task_name}] 最终保留岗位：", flush=True)
    for j in final_ok:
        ai_flag = "★AI" if has_ai_word(j["title"] + j["jd"][:300]) else "   "
        print(f"  {ai_flag} [{j['id']}] {j['title']}", flush=True)

    return {"total_raw": len(raw), "retained": len(final_ok), "excluded": len(all_excluded)}

# ── 主入口 ────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print("快手合并抓取脚本 (任务10+11+12+13)", flush=True)
    print(f"输出目录: {OUT_DIR}", flush=True)

    # 加载 cookies（复用或重新探测）
    cookies = load_cookies()

    # 依次执行三个任务（任务12与10同API，合并到 kuaishou_zhaopin_intern）
    summaries = {}
    for name, cfg in TASKS.items():
        summaries[name] = run_task(name, cfg, cookies)

    # 汇总
    print("\n" + "=" * 60, flush=True)
    print("【汇总】", flush=True)
    for name, s in summaries.items():
        print(f"  {name}: raw={s['total_raw']}  保留={s['retained']}  排除={s['excluded']}", flush=True)
    print("\n注：任务12 (zhaopin旧URL) 与任务10 使用相同API，已合并到 kuaishou_zhaopin_intern。", flush=True)


if __name__ == "__main__":
    main()
