#!/usr/bin/env python3
"""
任务13：快手社招岗位抓取与筛选
来源：zhaopin.kuaishou.cn
  positionNatureCode=C001（社招）, positionCategoryCode=J0005（产品）
  recruitProject=socialr
"""
import json, re, time, os, sys
from urllib.request import Request, urlopen
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


OUT_DIR = get_jobs_dir()
RAW_FILE  = os.path.join(OUT_DIR, "kuaishou_zhaopin_social_raw.json")
OUT_FILE  = os.path.join(OUT_DIR, "kuaishou_zhaopin_social_jobs.json")
EXCL_FILE = os.path.join(OUT_DIR, "kuaishou_zhaopin_social_excluded.json")
os.makedirs(OUT_DIR, exist_ok=True)

API  = "https://zhaopin.kuaishou.cn/recruit/e/api/v1/open/positions/simple"
ROOT = "https://zhaopin.kuaishou.cn/"
UA   = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
QS   = "positionCategoryCode=J0005&positionNatureCode=C001&recruitProject=socialr&workLocationCode=domestic"
DETAIL_TMPL = "https://zhaopin.kuaishou.cn/recruit/e/#/official/social/?jobId={job_id}"

# ── Cookies ──────────────────────────────────────────────────────────────────
def load_cookie() -> str:
    probe = "/tmp/ks_probe.json"
    if os.path.exists(probe) and (time.time() - os.path.getmtime(probe)) < 3600:
        d = json.load(open(probe))
        ck = d.get("zhaopin_social", {}).get("cookie_str", "") or d.get("zhaopin_trainee", {}).get("cookie_str", "")
        if ck:
            print(f"[Cookie] 复用 probe cookie (len={len(ck)})", flush=True)
            return ck
    print("[Cookie] 未找到有效 cookie，将不带 cookie 尝试", flush=True)
    return ""

# ── Step1: 拦截真实 API 响应分页抓取 ─────────────────────────────────────────
def fetch_all(cookie: str) -> list:
    if "--reuse-raw" in sys.argv and os.path.exists(RAW_FILE):
        print(f"[Step1] 复用 {RAW_FILE}", flush=True)
        return json.load(open(RAW_FILE))

    from playwright.sync_api import sync_playwright
    all_items, total = [], None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA)
        pg  = ctx.new_page()
        page_num = 1

        while True:
            captured = {}

            def on_resp(resp, _cap=captured):
                if "positions/simple" in resp.url and resp.status == 200:
                    try:
                        body = resp.text()
                        d = json.loads(body)
                        if d.get("code") == 0:
                            _cap["data"] = d
                    except: pass

            pg.on("response", on_resp)
            nav_url = (
                f"https://zhaopin.kuaishou.cn/recruit/e/#/official/social/"
                f"?workLocationCode=domestic&positionCategoryCode=J0005&recruitProject=socialr&pageNum={page_num}"
            )
            # 先清空再跳转，强制完整页面重载（避免 SPA hash 缓存不重新请求）
            pg.goto("about:blank")
            pg.goto(nav_url, wait_until="networkidle", timeout=45000)
            time.sleep(1.5)
            pg.remove_listener("response", on_resp)

            if "data" not in captured:
                print(f"[Step1] page={page_num} 未捕获到响应，停止", flush=True)
                break

            result = captured["data"].get("result") or {}
            items  = result.get("list") or []
            if total is None:
                total = result.get("total") or 0
            all_items.extend(items)
            print(f"[Step1] page={page_num}  fetched={len(all_items)}/{total}", flush=True)

            if len(all_items) >= total or len(items) == 0:
                break
            page_num += 1

        browser.close()

    seen, out = set(), []
    for it in all_items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    json.dump(out, open(RAW_FILE, "w"), ensure_ascii=False, indent=2)
    print(f"[Step1] raw {len(out)} 条 → {RAW_FILE}", flush=True)
    return out

# ── 关键词 ────────────────────────────────────────────────────────────────────
AI_KW   = ["AI","人工智能","大模型","LLM","GPT","AIGC","NLP","语音","语义",
           "机器学习","深度学习","算法","推荐系统","搜索","向量","Embedding",
           "智能","Agent","多模态","生成式","知识图谱","RAG","强化学习",
           "自然语言","计算机视觉","OCR","ASR","TTS","意图","问答"]
PROD_KW = ["产品经理","产品设计","需求分析","需求文档","PRD","原型","用户研究",
           "用户体验","用户调研","用户增长","产品规划","产品迭代","产品策略",
           "产品运营","商业化","生态","Axure","Figma"]

def kw_extr(text, max_k=8):
    out, seen = [], set()
    for w in AI_KW + PROD_KW + ["产品","策略","数据","增长","平台","电商","海外"]:
        if len(out) >= max_k: break
        if w.lower() in text.lower() and w not in seen:
            seen.add(w); out.append(w)
    return out[:max_k]

def has_ai(t): return any(w.lower() in t.lower() for w in AI_KW)
def has_prod(t):
    for w in PROD_KW:
        if w.lower() in t.lower(): return True
    return bool(re.search(r"(?<![A-Za-z])PM(?![A-Za-z])", t))

# ── Step2: 结构化 ─────────────────────────────────────────────────────────────
def structurize(raw):
    jobs = []
    for it in raw:
        desc   = (it.get("description") or "").strip()
        demand = (it.get("positionDemand") or "").strip()
        jd = ("【岗位职责】\n"+desc if desc else "") + ("\n\n【任职要求】\n"+demand if demand else "")
        locs = [ld.get("name","") for ld in (it.get("workLocationDicts") or []) if isinstance(ld,dict) and ld.get("name")]
        loc = "、".join(locs) or it.get("workLocationCode","未知")
        job_id = str(it.get("id",""))
        first  = re.sub(r"^[0-9、．.\s]+","",desc.split("\n")[0].strip())[:80] if desc else it.get("name","")
        jobs.append({
            "id": job_id,
            "title": it.get("name",""),
            "location": loc,
            "category": it.get("positionCategoryCode",""),
            "type": "实习",
            "url": DETAIL_TMPL.format(job_id=job_id),
            "keywords": kw_extr(jd),
            "summary": first,
            "jd": jd,
        })
    return jobs

# ── 筛选 ──────────────────────────────────────────────────────────────────────
EXCL_T = re.compile(r"(工程师|研发|算法工程|开发|架构|测试|运维|SRE|DBA|安全工程|网络|硬件|"
                    r"设计师|UX|UI|前端|后端|客户端|移动端|数据分析师|分析师|BI|科学家|研究员|"
                    r"法务|财务|会计|行政|人事|招聘|市场专员|品牌|公关|PR|媒体|销售|客服|"
                    r"内容运营|社区运营|活动运营)", re.I)
EXCL_EX = re.compile(r"技术产品|AI.*产品|产品.*AI|智能.*产品|产品.*智能", re.I)
TECH_H  = re.compile(r"(开发|编程|代码|工程|架构|部署|训练模型|算法工程|研发岗|研究员)", re.I)
TECH_S  = re.compile(r"(推荐系统|搜索引擎|AI|人工智能|大模型|NLP|机器学习|深度学习|LLM|智能|Agent)", re.I)
PROD_S  = re.compile(r"(产品经理|需求.*分析|用户.*研究|PRD|产品设计|用户.*体验|产品.*规划|"
                     r"路线图|Roadmap|原型|Axure|商业化|产品策略|产品迭代)", re.I)

def stage1(jobs):
    ok, ex = [], []
    for j in jobs:
        t, jd5 = j["title"], j["jd"][:500]
        if EXCL_T.search(t) and not EXCL_EX.search(t):
            j["stage1_reason"] = f"title-excl:{EXCL_T.search(t).group()}"; ex.append(j)
        elif not has_prod(t) and not has_prod(jd5):
            j["stage1_reason"] = "no-product-word"; ex.append(j)
        else:
            ok.append(j)
    return ok, ex

def stage2(jobs):
    ok, ex = [], []
    for j in jobs:
        jd = j["jd"]
        th = len(TECH_H.findall(jd)); ts = len(TECH_S.findall(jd)); pr = len(PROD_S.findall(jd))
        j["score"] = {"tech_hard":th,"tech_soft":ts,"prod":pr}
        if th >= 5 and pr == 0:
            j["stage2_reason"] = f"tech_hard={th} prod=0"; ex.append(j)
        elif pr == 0 and ts == 0:
            j["stage2_reason"] = "no-prod-no-ai"; ex.append(j)
        else:
            ok.append(j)
    return ok, ex

# Stage3：初始为空，运行后根据AI信号强弱手工填入
STAGE3_EXCL: set = set()

def stage3(jobs):
    ok, ex = [], []
    for j in jobs:
        if j["id"] in STAGE3_EXCL:
            j["stage3_reason"] = "手工排除"; ex.append(j)
        else:
            ok.append(j)
    return ok, ex

def verify_urls(jobs):
    print(f"[Step6] SPA 根路径验证: {ROOT}", flush=True)
    try:
        with urlopen(Request(ROOT, headers={"User-Agent":UA}), timeout=10) as r:
            ok = r.status == 200
    except: ok = False
    print(f"[Step6] → HTTP {'200 ✓' if ok else '× 失败'}", flush=True)
    for j in jobs: j["url_ok"] = ok

def main():
    print("="*60, flush=True)
    print("[任务13] 快手社招", flush=True)
    cookie = load_cookie()
    raw    = fetch_all(cookie)
    jobs   = structurize(raw)
    print(f"[Step2] 结构化 {len(jobs)} 条", flush=True)

    s1_ok, s1_ex = stage1(jobs)
    print(f"[Stage1] 保留 {len(s1_ok)}  排除 {len(s1_ex)}", flush=True)
    for j in s1_ex: print(f"  ✗ {j['title']}  ({j.get('stage1_reason','')})", flush=True)

    s2_ok, s2_ex = stage2(s1_ok)
    print(f"[Stage2] 保留 {len(s2_ok)}  排除 {len(s2_ex)}", flush=True)
    for j in s2_ex: print(f"  ✗ {j['title']}  ({j.get('stage2_reason','')})", flush=True)

    final, s3_ex = stage3(s2_ok)
    verify_urls(final)

    json.dump(final,            open(OUT_FILE,  "w"), ensure_ascii=False, indent=2)
    json.dump(s1_ex+s2_ex+s3_ex, open(EXCL_FILE,"w"), ensure_ascii=False, indent=2)
    print(f"\n[Done] 保留 {len(final)}  排除 {len(s1_ex+s2_ex+s3_ex)}", flush=True)
    print(f"  → {OUT_FILE}", flush=True)

    print("\n[待Stage3复核的保留岗位]", flush=True)
    for j in final:
        flag = "★AI" if has_ai(j["title"]+j["jd"][:300]) else "   "
        print(f"  {flag} [{j['id']}] {j['title']}", flush=True)

if __name__ == "__main__":
    main()
