#!/usr/bin/env python3
"""
京东社招公开通道：/web/job/job_info_list/3（与用户给的 /web/job_info_list/3 同入口目的，旧路径常 302 passport）
POST /web/job/job_list 分页拉齐 → Stage1～3 → urllib 校验详情（同 Cookie 会话）
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


用法：
  python3 jd_social_scrape.py              # 重新拉全量 RAW + 筛选 + 校验
  python3 jd_social_scrape.py --reuse-raw  # 仅读 /Users/Shared/jd_social_raw.json 做筛选与校验（需已存在）
"""
import argparse
import http.cookiejar
import json
import os
import re
import shutil
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor

LIST_REF = "https://zhaopin.jd.com/web/job/job_info_list/3?isHunterFlag=false"
API_LIST = "https://zhaopin.jd.com/web/job/job_list"
DETAIL_TMPL = "https://zhaopin.jd.com/web/job-info-detail?requementId={rid}"
RAW_PATH_DEFAULT = "/Users/Shared/jd_social_raw.json"


def make_opener():
    return build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))


def fetch_page(opener, page_index: int, page_size: int = 100) -> list:
    body = urlencode(
        {
            "pageIndex": page_index,
            "pageSize": page_size,
            "workCityJson": "[]",
            "jobTypeJson": "[]",
            "jobSearch": "",
            "depTypeJson": "[]",
        }
    ).encode("utf-8")
    req = Request(
        API_LIST,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": LIST_REF,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        },
    )
    with opener.open(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_all_merged(opener) -> list:
    by_rid: dict[int, dict] = {}
    p = 1
    while True:
        lst = fetch_page(opener, p, 100)
        time.sleep(0.04)
        if not lst:
            break
        for it in lst:
            rid = it.get("requirementId")
            if rid is not None:
                by_rid[int(rid)] = it
        if len(lst) < 100:
            break
        p += 1
        if p > 500:
            raise RuntimeError("分页超过 500")
    return sorted(by_rid.values(), key=lambda x: int(x.get("requirementId") or 0))


def jd_full(it: dict) -> str:
    duty = (it.get("workContent") or "").strip()
    qual = (it.get("qualification") or "").strip()
    parts = []
    if duty:
        parts.append("岗位职责:\n" + duty)
    if qual:
        parts.append("\n任职要求:\n" + qual)
    return "\n".join(parts).strip()


AI_TITLE = re.compile(
    r"(?<![A-Za-z])AI(?![A-Za-z])|大模型|[Ll]{2}[Mm]|[Aa]gent|AIGC|多模态|人工智能|Prompt|向量|"
    r"智能体|智能化|机器学习|深度学习|推理|评测|Copilot|MCP|算法策略产品",
    re.I,
)
EXCL_TITLE = re.compile(
    r"算法工程师|^算法(?!产品)|视觉设计|交互设计|^设计师|法务|\bHR\b|人力专员",
    re.I,
)
JD_AI = re.compile(
    r"(?<![A-Za-z])AI(?![A-Za-z])|大模型|LLM|[Aa]gent|智能体|AIGC|多模态|人工智能|机器学习|深度学习|"
    r"Prompt|向量|推理|Copilot|MCP|RAG",
    re.I,
)
TECH = {
    3: re.compile(r"预训练|RLHF|SFT|CUDA|分布式训练|MLOps|GPU集群|算子", re.I),
    2: re.compile(r"PaaS|底层架构", re.I),
    1: re.compile(r"基础设施", re.I),
}
PROD_SIG = re.compile(
    r"产品规划|需求|用户体验|商业化|路线图|竞品|产品设计|原型|PRD|调研|场景|迭代|协同|数据分析|交付|闭环|立项",
    re.I,
)


def pm_title_ok(name: str) -> bool:
    n = name.replace(" ", "")
    if re.search(r"产品经理|产品负责人|产品专家|产品策划|产品规划", n):
        return True
    if "PM" in n and "产品" in n:
        return True
    return False


def stage1(name: str, jd_text: str) -> tuple[bool, str]:
    n = name.replace(" ", "")
    if not (name or "").strip():
        return False, "无标题"
    if EXCL_TITLE.search(name.strip()):
        return False, "标题硬排除"
    if "产品运营" in n and ("产品经理" not in n) and ("产品专家" not in n):
        return False, "产品运营为主"
    if not pm_title_ok(name):
        return False, "标题无产品经理/规划/专家主轴词"
    if AI_TITLE.search(name):
        return True, "标题AI信号"
    duty = jd_text.split("\n任职要求:\n")[0] if "\n任职要求:\n" in jd_text else jd_text
    if JD_AI.search((duty or "")[:520]):
        return True, "JD职责前520字AI信号"
    return False, "标题与职责均无AI主轴信号"


def stage2(jd_text: str) -> tuple[bool, int, int, str]:
    ts = sum(w * len(p.findall(jd_text)) for w, p in [(3, TECH[3]), (2, TECH[2]), (1, TECH[1])])
    ps = len(PROD_SIG.findall(jd_text))
    if ts >= 7:
        return True, ts, ps, "tech>=7"
    if ts >= 5 and ps <= 1:
        return True, ts, ps, "tech>=5&prod<=1"
    return False, ts, ps, ""


def kw_extr(title: str, jd_text: str, max_k=8):
    seen, out = set(), []
    for pat in [
        r"大模型|Agent|LLM|AIGC|多模态|RAG|Prompt|评测|智能体|云服务|Compliance|合规",
        r"产品经理|产品设计|竞品|用户需求|商业化|PRD|数据产品|解决方案|B端|C端|策略",
    ]:
        for m in re.finditer(pat, title + jd_text, re.I):
            k = m.group(0)
            lk = k.lower()
            if lk not in seen and len(out) < max_k:
                seen.add(lk)
                out.append(k)
    for z in ("京东社招", "AI产品", "产品经理", "社招", "AI"):
        if len(out) >= max_k:
            break
        lz = z.lower()
        if lz not in seen:
            seen.add(lz)
            out.append(z)
    return out[:max_k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-raw", action="store_true")
    args = ap.parse_args()

    opener = make_opener()

    if args.reuse_raw and os.path.isfile(RAW_PATH_DEFAULT):
        with open(RAW_PATH_DEFAULT, encoding="utf-8") as f:
            lst = json.load(f)["list"]
        print("(reuse raw)", len(lst), flush=True)
    else:
        lst = fetch_all_merged(opener)
        with open(RAW_PATH_DEFAULT, "w", encoding="utf-8") as f:
            json.dump(
                {"source_list_url": LIST_REF, "api": API_LIST, "count": len(lst), "list": lst},
                f,
                ensure_ascii=False,
                indent=2,
            )
        print("(fetched)", len(lst), flush=True)

    s1_keep, excludes = [], []
    for it in lst:
        title = it.get("positionName") or ""
        jd_text = jd_full(it)
        ok, why = stage1(title, jd_text)
        if ok:
            s1_keep.append(it)
        else:
            excludes.append((it.get("requirementId"), title, why))

    out = []
    for it in s1_keep:
        jd_text = jd_full(it)
        bad, ts, ps, rr = stage2(jd_text)
        if bad:
            excludes.append((it.get("requirementId"), it.get("positionName"), f"stage2:{rr} t={ts} p={ps}"))
            continue

        rid = int(it["requirementId"])
        title = it.get("positionName") or ""
        firstduty = ((it.get("workContent") or "").strip().split("\n")[0] or title).strip()
        ai_cat = (
            "AI原生"
            if re.search(r"Agent|LLM|大模型|AIGC|智能体|MCP", title + jd_text, re.I)
            else "AI赋能"
        )

        item = {
            "id": str(len(out) + 1),
            "company": "京东",
            "bu": it.get("positionDeptName") or "京东",
            "title": title,
            "city": it.get("workCity") or "",
            "type": "社招",
            "url": DETAIL_TMPL.format(rid=rid),
            "jd": jd_text,
            "cats": {
                "AI类型": ai_cat,
                "业务方向": it.get("positionDeptName") or "京东",
                "官网职位类别字段": it.get("jobType") or "",
                "招聘列表聚合页": LIST_REF,
                "requementId": str(rid),
            },
            "summary": (firstduty[:160] + "…") if len(firstduty) > 160 else firstduty,
            "keywords": [x for x in kw_extr(title, jd_text) if x][:8],
            "requirementId": str(rid),
            "portal_list_note": LIST_REF,
        }
        out.append(item)

    print(f"retained {len(out)}  excluded {len(excludes)}", flush=True)

    # 列表页 GET（等价于监督清单 Step 6 聚合页验证）
    r0 = opener.open(Request(LIST_REF, headers={"User-Agent": "Mozilla/5.0"}), timeout=30)
    if r0.status != 200:
        raise RuntimeError(f"列表页 HTTP {r0.status}")
    r0.read()
    print("list_page_200 ok", flush=True)

    # Warm session cookie
    fetch_page(opener, 1, 3)
    time.sleep(0.05)

    bad_urls = []
    for i, rec in enumerate(out):
        if i % 10 == 0:
            print(f"url_check {i}/{len(out)}", flush=True)
        rq = Request(rec["url"], headers={"User-Agent": "Mozilla/5.0", "Referer": LIST_REF})
        try:
            with opener.open(rq, timeout=20) as r:
                body = r.read()
                if r.status != 200 or len(body) < 2800:
                    bad_urls.append((rec["requirementId"], r.status, len(body)))
        except Exception as e:
            bad_urls.append((rec["requirementId"], type(e).__name__, str(e)[:90]))
        time.sleep(0.03)

    print(f"url_check done  bad={len(bad_urls)}", flush=True)
    if bad_urls:
        raise RuntimeError(f"详情 URL 校验失败: {bad_urls[:8]}")

    out_path = os.path.join(get_jobs_dir(), "jd_social_jobs.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    excl_path = os.path.join(get_jobs_dir(), "jd_social_excluded.json")
    with open(excl_path, "w", encoding="utf-8") as f:
        json.dump(excludes, f, ensure_ascii=False, indent=2)

    try:
        shutil.copy2(out_path, "/Users/Shared/Desktop/岗位搜集/jobs/jd_social_jobs.json")
    except Exception:
        pass

    print(json.dumps({"out_path": out_path, "retained": len(out), "raw_note": RAW_PATH_DEFAULT}, ensure_ascii=False))


if __name__ == "__main__":
    main()
