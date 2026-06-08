#!/usr/bin/env python3
"""
任务01：腾讯 AI PM 岗位抓取与筛选
API: GET /tencentcareer/api/post/Query?categoryId=40003001
"""
import json, re, time, os, sys
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


OUT_DIR = get_jobs_dir()
RAW_FILE  = os.path.join(OUT_DIR, "tencent_raw.json")
OUT_FILE  = os.path.join(OUT_DIR, "tencent_jobs.json")
EXCL_FILE = os.path.join(OUT_DIR, "tencent_excluded.json")
os.makedirs(OUT_DIR, exist_ok=True)

BASE    = "https://careers.tencent.com/tencentcareer/api"
UA      = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"
REFERER = "https://careers.tencent.com/search.html?query=ot_40003001"

def ts(): return int(time.time() * 1000)

def fetch_list() -> list:
    """抓取所有产品岗列表（categoryId=40003001），仅保留中国区"""
    all_posts, page = [], 1
    while True:
        url = (f"{BASE}/post/Query?timestamp={ts()}"
               f"&countryId=&cityId=&bgIds=&productId="
               f"&categoryId=40003001&parentCategoryId=&attrId=&keyword="
               f"&pageIndex={page}&pageSize=100&language=zh-cn")
        req = Request(url, headers={"User-Agent": UA, "Referer": REFERER})
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        posts = data["Data"]["Posts"]
        total = data["Data"]["Count"]
        all_posts.extend(posts)
        print(f"[Step1] page={page}  fetched={len(all_posts)}/{total}", flush=True)
        if len(all_posts) >= total or len(posts) == 0:
            break
        page += 1
        time.sleep(0.3)
    # 只保留中国区
    cn = [p for p in all_posts if p.get("CountryName") in ("中国", "")]
    print(f"[Step1] 中国区: {len(cn)}/{len(all_posts)} 条", flush=True)
    return cn

def fetch_detail(post_id: str) -> dict:
    url = f"{BASE}/post/ByPostId?timestamp={ts()}&postId={post_id}&language=zh-cn"
    req = Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    try:
        with urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        return d.get("Data") or {}
    except Exception as e:
        return {}

# ── Step2 结构化 ──────────────────────────────────────────────────────────────
AI_KW   = ["AI","人工智能","大模型","LLM","GPT","AIGC","NLP","语音","语义",
           "机器学习","深度学习","算法","推荐系统","搜索","向量","Embedding",
           "智能","Agent","多模态","生成式","知识图谱","RAG","强化学习"]
PROD_KW = ["产品经理","产品设计","需求分析","需求文档","PRD","原型","用户研究",
           "用户体验","用户调研","产品规划","产品迭代","产品策略","商业化","Axure"]

def kw_extr(t, n=8):
    out, seen = [], set()
    for w in AI_KW + PROD_KW + ["产品","策略","数据","增长","平台"]:
        if len(out) >= n: break
        if w.lower() in t.lower() and w not in seen:
            seen.add(w); out.append(w)
    return out[:n]

def has_ai(t):   return any(w.lower() in t.lower() for w in AI_KW)
def has_prod(t):
    for w in PROD_KW:
        if w.lower() in t.lower(): return True
    return bool(re.search(r"(?<![A-Za-z])PM(?![A-Za-z])", t))

def structurize(raw: list) -> list:
    print(f"[Step2] 拉取 {len(raw)} 条详情...", flush=True)
    details = {}
    def _fetch(p):
        return p["PostId"], fetch_detail(p["PostId"])
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch, p): p["PostId"] for p in raw}
        done = 0
        for fut in as_completed(futs):
            pid, det = fut.result()
            details[pid] = det
            done += 1
            if done % 20 == 0:
                print(f"  详情: {done}/{len(raw)}", flush=True)

    jobs = []
    for p in raw:
        det  = details.get(p["PostId"], {})
        resp = (det.get("Responsibility") or p.get("Responsibility") or "").strip()
        req_ = (det.get("Requirement") or "").strip()
        jd   = ("【岗位职责】\n" + resp if resp else "") + \
               ("\n\n【任职要求】\n" + req_ if req_ else "")
        first = re.sub(r"^[0-9、．.\s]+", "", resp.split("\n")[0].strip())[:80] if resp else p.get("RecruitPostName","")
        jobs.append({
            "id":       p["PostId"],
            "title":    p.get("RecruitPostName", ""),
            "bg":       p.get("BGName", ""),
            "product":  p.get("ProductName", ""),
            "location": p.get("LocationName", ""),
            "type":     "社招" if p.get("SourceID") == 1 else "校招",
            "url":      p.get("PostURL", f"https://careers.tencent.com/jobdesc.html?postId={p['PostId']}"),
            "keywords": kw_extr(jd),
            "summary":  first,
            "jd":       jd,
        })
    return jobs

# ── Stage1 ────────────────────────────────────────────────────────────────────
EXCL_T  = re.compile(r"(工程师|研发|算法工程|测试工程|运维|设计师|UX|UI|研究员|财务|法务|行政|人事|招聘|销售|客服专员|市场专员)", re.I)
EXCL_EX = re.compile(r"(AI产品|AIGC产品|产品.*AI|智能.*产品|技术产品)", re.I)

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

# ── Stage2 ────────────────────────────────────────────────────────────────────
TECH_H = re.compile(r"(开发|编程|代码|工程|架构|部署|训练模型|算法工程|研发岗)", re.I)
TECH_S = re.compile(r"(推荐系统|搜索引擎|AI|人工智能|大模型|NLP|机器学习|深度学习|LLM|智能|Agent)", re.I)
PROD_S = re.compile(r"(产品经理|需求.*分析|用户.*研究|PRD|产品设计|用户.*体验|产品.*规划|原型|Axure|商业化|产品策略)", re.I)

def stage2(jobs):
    ok, ex = [], []
    for j in jobs:
        jd = j["jd"]
        th = len(TECH_H.findall(jd)); ts_ = len(TECH_S.findall(jd)); pr = len(PROD_S.findall(jd))
        j["score"] = {"tech_hard": th, "tech_soft": ts_, "prod": pr}
        if th >= 5 and pr == 0:
            j["stage2_reason"] = f"tech_hard={th} prod=0"; ex.append(j)
        elif pr == 0 and ts_ == 0:
            j["stage2_reason"] = "no-prod-no-ai"; ex.append(j)
        else:
            ok.append(j)
    return ok, ex

# ── Stage3 ────────────────────────────────────────────────────────────────────
STAGE3_EXCL: set = set()   # 初始为空，运行后根据复核结果填入

def stage3(jobs):
    ok = [j for j in jobs if j["id"] not in STAGE3_EXCL]
    ex = [j for j in jobs if j["id"] in STAGE3_EXCL]
    for j in ex: j["stage3_reason"] = "Stage3手工排除"
    return ok, ex

# ── Step6 URL 验证 ─────────────────────────────────────────────────────────────
def verify_urls(jobs):
    root = "https://careers.tencent.com/"
    try:
        with urlopen(Request(root, headers={"User-Agent": UA}), timeout=10) as r:
            print(f"[Step6] {root} → HTTP {r.status}", flush=True)
            ok = r.status == 200
    except Exception as e:
        print(f"[Step6] 验证失败: {e}", flush=True); ok = False
    for j in jobs: j["url_ok"] = ok

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print("[任务01] 腾讯 AI PM 岗位", flush=True)

    if "--reuse-raw" in sys.argv and os.path.exists(RAW_FILE):
        print(f"[Step1] 复用 {RAW_FILE}", flush=True)
        raw = json.load(open(RAW_FILE))
    else:
        raw = fetch_list()
        json.dump(raw, open(RAW_FILE, "w"), ensure_ascii=False, indent=2)
        print(f"[Step1] raw {len(raw)} 条 → {RAW_FILE}", flush=True)

    jobs = structurize(raw)
    print(f"[Step2] 结构化 {len(jobs)} 条", flush=True)

    s1_ok, s1_ex = stage1(jobs)
    print(f"[Stage1] 保留 {len(s1_ok)}  排除 {len(s1_ex)}", flush=True)
    for j in s1_ex: print(f"  ✗ {j['title']}  ({j.get('stage1_reason','')})", flush=True)

    s2_ok, s2_ex = stage2(s1_ok)
    print(f"[Stage2] 保留 {len(s2_ok)}  排除 {len(s2_ex)}", flush=True)
    for j in s2_ex: print(f"  ✗ {j['title']}  ({j.get('stage2_reason','')})", flush=True)

    final, s3_ex = stage3(s2_ok)
    verify_urls(final)

    json.dump(final,              open(OUT_FILE,  "w"), ensure_ascii=False, indent=2)
    json.dump(s1_ex+s2_ex+s3_ex, open(EXCL_FILE, "w"), ensure_ascii=False, indent=2)
    print(f"\n[Done] 保留 {len(final)}  排除 {len(s1_ex+s2_ex+s3_ex)}", flush=True)
    print(f"  → {OUT_FILE}", flush=True)

    print("\n[待Stage3复核]", flush=True)
    for j in final:
        flag = "★AI" if has_ai(j["title"] + j["jd"][:300]) else "   "
        print(f"  {flag} [{j['id']}] {j['title']}  [{j['bg']}]", flush=True)

if __name__ == "__main__":
    main()
