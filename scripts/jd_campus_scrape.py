#!/usr/bin/env python3
"""
京东校招（campus.jd.com）指定培养项目 planId=45 → 全量 RAW → aipmjobs Stage1～3。
岗位页为 SPA，官方「详情」不落独立 HTTP 路径；逐条 urllib 等价校验见 main()。
"""
import json
import os
import re
import shutil
from urllib.request import Request, urlopen
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from jobs_dir import get_jobs_dir


API_PAGE = "https://campus.jd.com/api/wx/position/page?type=present"
LIST_REF = "https://campus.jd.com/#/jobs?selProjects=45"
ORIGIN = "https://campus.jd.com"


def fetch_all_pages(plan_ids: list[str]) -> tuple[list[dict], dict]:
    """分页到底；planIdList 与用户 URL selProjects 一致。"""
    merged: dict[int, dict] = {}
    meta = {}
    page_index = 0
    page_size = 120
    while True:
        body = {
            "pageSize": page_size,
            "pageIndex": page_index,
            "parameter": {
                "positionName": "",
                "planIdList": plan_ids,
                "jobDirectionCodeList": [],
                "workCityCodeList": [],
                "positionDeptList": [],
            },
        }
        data = json.dumps(body).encode("utf-8")
        req = Request(
            API_PAGE,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Referer": ORIGIN + "/",
                "Origin": ORIGIN,
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            },
        )
        with urlopen(req, timeout=90) as r:
            raw = json.loads(r.read())
        if not raw.get("success"):
            raise RuntimeError(f"API success=false {raw}")
        data_b = raw.get("body") or {}
        lst = data_b.get("items") or []
        meta = {"totalNumber": data_b.get("totalNumber"), "fetched_round": page_index}
        for it in lst:
            pid = it.get("publishId")
            if pid is not None:
                merged[int(pid)] = it
        if len(lst) < page_size:
            break
        page_index += 1
        if page_index > 80:
            break
    return sorted(merged.values(), key=lambda x: int(x.get("publishId") or 0)), meta


def job_public_url(publish_id):
    """SPA 深链；浏览器内打开后需在列表中定位该 publishId（站点多数情况下不暴露独立 HTML 详情页）。"""
    return f"https://campus.jd.com/#/jobs?selProjects=45&publishId={publish_id}"


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
    r"(?<![A-Za-z])AI(?![A-Za-z])|[Aa]\.?I\.?|大模型|[Ll]{2}[Mm]|[Aa]gent|"
    r"AIGC|多模态|人工智能|机器学习|深度学习|Prompt|向量|智能体|智能化|推理|评测|Copilot|MCP",
    re.I,
)
EXCL_TITLE = re.compile(
    r"算法工程师|研发工程师|数据工程师|测试工程师|运维工程师|视觉设计|交互设计|"
    r"设计师|法务专员|人力资源专员|\bHR\b|招聘专员|数据分析师|"
    r"前端开发|后端开发|客户端开发|开发工程师|安全工程师|机械结构|硬件工程师|芯片|"
    r"(?<!产品)工程师(?!经理)|研究员",
    re.I,
)


def has_product_word(title: str) -> bool:
    t = title.replace(" ", "")
    return bool(
        re.search(r"产品经理|产品专家|产品规划|产品策划|产品实习|产品方向|PM\b|Product", t, re.I)
        or ("产品" in t)
    )


JD_AI_DUTY = re.compile(
    r"(?<![A-Za-z])AI(?![A-Za-z])|大模型|LLM|[Aa]gent|智能体|AIGC|多模态|人工智能|机器学习|深度学习|"
    r"Prompt|向量|推理|Copilot|MCP|RAG|RLHF",
    re.I,
)

TECH = {
    3: re.compile(
        r"预训练|RLHF|SFT|模型蒸馏|分布式训练|CUDA|算力|GPU|MLOps|推理优化"
        r"|向量数据库(?![^。]{0,20}体验)|算子(?![^。]{0,30}产品经理)",
        re.I,
    ),
    2: re.compile(r"PaaS(?![^。\n]{0,40}billing)|底层架构", re.I),
    1: re.compile(r"基础设施(?![^。\n]{0,30}用户)", re.I),
}
PROD_SIG = re.compile(
    r"产品规划|需求分析|用户需求|原型|PRD|用户调研|产品迭代|产品方案|产品策略|路线图|竞品|商业化|用户体验|交互"
    r"|产品设计|产品设计|立项|优先级|ROI|闭环|落地",
    re.I,
)


def stage1(title: str, jd_text: str) -> tuple[bool, str]:
    raw_title = title
    title = raw_title.strip()
    if not title:
        return False, "无标题"

    low = raw_title.strip()
    if EXCL_TITLE.search(low) and ("产品经理" not in low.replace(" ", "")) and ("产品专家" not in low.replace(" ", "")):
        return False, "标题硬排除工种"

    if not has_product_word(title):
        return False, "标题无产品主轴词"

    t2 = title.replace(" ", "")
    if ("产品运营" in t2 or "运营" in t2) and ("产品经理" not in t2) and ("产品专家" not in t2):
        if "产品运营" in t2 or ("运营" in t2 and "产品" not in t2):
            if "产品运营" in t2:
                return False, "产品运营岗为主"
            return False, "运营向岗位且非产品经理"

    if AI_TITLE.search(title):
        return True, "标题AI信号"

    duty = ""
    if "岗位职责:" in jd_text:
        duty = jd_text.split("任职要求:")[0] if "任职要求:" in jd_text else jd_text
    else:
        duty = jd_text[:800]

    if JD_AI_DUTY.search((duty or "")[:500]):
        return True, "职责前500字命中AI主轴词"

    if re.search(r"产品经理|产品专家", title.replace(" ", "")) and JD_AI_DUTY.search((duty or "")[:400]):
        return True, "泛产品岗+职责含AI主轴"

    return False, "标题与职责均无AI主线信号"


def stage2(jd_text: str) -> tuple[bool, int, int, str]:
    """返回 (是否剔除, tech分, prod信号次数, 规则码)。"""
    ts = sum(w * len(p.findall(jd_text)) for w, p in [(3, TECH[3]), (2, TECH[2]), (1, TECH[1])])
    ps = len(PROD_SIG.findall(jd_text))
    if ts >= 7:
        return True, ts, ps, "tech>=7"
    if ts >= 5 and ps <= 1:
        return True, ts, ps, "tech>=5&prod<=1"
    return False, ts, ps, ""


def city_str(it: dict) -> str:
    wc = it.get("workCity")
    if isinstance(wc, str):
        return wc
    if isinstance(wc, list) and wc:
        return ",".join(str(x.get("name") if isinstance(x, dict) else x) for x in wc)
    return ""


def bu_str(it: dict) -> str:
    return (
        it.get("positionDept")
        or it.get("jobDirection")
        or it.get("jobCategory")
        or "京东"
    )


def kw_extr(title: str, jd_text: str, max_k=8):
    seen, out = set(), []
    for pat in [
        r"大模型|Agent|LLM|AIGC|多模态|RAG|Prompt|智能体|Copilot|MCP|工作流|Python|自动化",
        r"产品经理|用户需求|业务流程|产品设计|竞品|商业化|数据分析|迭代|PRD|人力资源",
    ]:
        for m in re.finditer(pat, title + jd_text, re.I):
            k = m.group(0)
            lk = k.lower()
            if lk not in seen and len(out) < max_k:
                seen.add(lk)
                out.append(k)
    while len(out) < max_k:
        for z in ("京东校招", "AI产品", "产品经理", "校招"):
            if z.lower() not in seen:
                out.append(z)
                seen.add(z.lower())
    return out[:max_k]


def refresh_list_json() -> str:
    """用于「岗位仍在招聘列表中」的等价链接校验（SPA 无独立详情 HTML）。"""
    body = {
        "pageSize": 400,
        "pageIndex": 0,
        "parameter": {
            "positionName": "",
            "planIdList": ["45"],
            "jobDirectionCodeList": [],
            "workCityCodeList": [],
            "positionDeptList": [],
        },
    }
    data = json.dumps(body).encode("utf-8")
    req = Request(
        API_PAGE,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Referer": ORIGIN + "/",
            "Origin": ORIGIN,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        },
    )
    with urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8")


def main():
    plan = ["45"]
    lst, meta = fetch_all_pages(plan)

    raw_path = "/Users/Shared/jd_campus_plan45_raw.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "list": lst, "source_url": LIST_REF, "planIdList": plan}, f, ensure_ascii=False, indent=2)

    s1_keep, excludes = [], []
    for it in lst:
        title = it.get("positionName") or ""
        jd_text = jd_full(it)
        ok, why = stage1(title, jd_text)
        if ok:
            s1_keep.append((it, why))
        else:
            excludes.append((it.get("publishId"), title, why))

    s3_skip: set[int] = set()

    out = []
    for it, _note in s1_keep:
        pid = int(it.get("publishId") or 0)
        if pid in s3_skip:
            excludes.append((pid, it.get("positionName"), "stage3:边界岗非PM主轴"))
            continue
        jd_text = jd_full(it)
        first_line = ((it.get("workContent") or "").strip().split("\n")[0] or it.get("positionName") or "").strip()
        excl, ts, ps, rr = stage2(jd_text)
        if excl:
            excludes.append((pid, it.get("positionName"), f"stage2:{rr} t={ts} p={ps}"))
            continue

        title = it.get("positionName") or ""
        ai_cat = (
            "AI原生"
            if re.search(r"Agent|LLM|大模型|AIGC|智能体|MCP", title + jd_text, re.I)
            else "AI赋能"
        )

        item = {
            "id": str(len(out) + 1),
            "company": "京东",
            "bu": bu_str(it),
            "title": title,
            "city": city_str(it),
            "type": "校招",
            "url": job_public_url(pid),
            "jd": jd_text,
            "cats": {
                "AI类型": ai_cat,
                "业务方向": bu_str(it),
                "岗位层级": "校招",
                "招聘官网聚合": LIST_REF,
                "培养项目planId": plan[0],
                "职位方向": it.get("jobDirection") or "",
                "publishId": str(pid),
            },
            "summary": (first_line[:150] + "…") if len(first_line) > 150 else first_line,
            "keywords": kw_extr(title, jd_text),
            "publishId": str(pid),
            "portal_list_note": LIST_REF,
        }
        item["keywords"] = [k for k in item["keywords"] if k][:8]
        out.append(item)

    list_snapshot = refresh_list_json()
    missing = [r["publishId"] for r in out if str(r["publishId"]) not in list_snapshot]
    if missing:
        raise RuntimeError(f"快照中缺少 publishId: {missing}")

    root_rq = Request(ORIGIN + "/", headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(root_rq, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f"首页 status {r.status}")

    bad_frags = []
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    for rec in out:
        try:
            rq = Request(rec["url"], headers={"User-Agent": UA, "Referer": ORIGIN + "/"})
            with urlopen(rq, timeout=25) as r:
                if r.status != 200 or len(r.read()) < 800:
                    bad_frags.append((rec["publishId"], r.status))
        except Exception as e:
            bad_frags.append((rec["publishId"], type(e).__name__))

    out_path = os.path.join(get_jobs_dir(), "jd_campus_jobs.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    excl_path = os.path.join(get_jobs_dir(), "jd_campus_excluded.json")
    with open(excl_path, "w", encoding="utf-8") as f:
        json.dump(excludes, f, ensure_ascii=False, indent=2)

    try:
        shutil.copy2(out_path, "/Users/Shared/Desktop/岗位搜集/jobs/jd_campus_jobs.json")
    except Exception:
        pass

    print(
        json.dumps(
            {
                "raw_count": len(lst),
                "raw_path": raw_path,
                "retained": len(out),
                "excluded": len(excludes),
                "list_api_publishId_check": "ok" if not missing else missing,
                "spa_get_root_status": 200,
                "fragment_url_open_status": bad_frags,
                "out": out_path,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()