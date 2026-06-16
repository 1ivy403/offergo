#!/usr/bin/env python3
"""
逐条抓取投递链接的真实岗位信息，写入 applied-manifest.json（extraJobs）。
不修改 jobs.json；不修改 apply-baseline.json（保留原 86 条已投）。
"""
from __future__ import annotations

import json
import re
import time
import html
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
JOBS_JSON = ROOT / "jobs.json"
MANIFEST = ROOT / "applied-manifest.json"
BASELINE = ROOT / "apply-baseline.json"
REPORT = ROOT / "scripts" / "scrape_applied_report.json"

OMIT = "未注明"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
START_ID = 600001

URLS = """
https://hr-campus.vivo.com/form/friendly?fromPage=job&CategoryId=3&submissionId=95886635
https://hr-campus.vivo.com/intern/detail?jobAdId=73d1af9f-17fc-4a00-ac02-72a2f7f8ec15
https://hr-campus.vivo.com/form?fromPage=job&jobAdId=4c86514f-0bb8-4753-b98b-b2127a3cf15f&userId=85217342
https://careers.oppo.com/university/oppo/campus/post/1725?recruitType=Graduate
https://jobs.bilibili.com/campus/positions/28582
https://jobs.bilibili.com/campus/positions/28157
https://jobs.bilibili.com/campus/positions/26074
https://jobs.bytedance.com/campus/position/7516801780115982610/detail
https://jobs.bytedance.com/campus/position/7602531028664600837/detail
https://jobs.bytedance.com/campus/position/7604808048792766773/detail
https://jobs.bytedance.com/campus/position/7618148265837611317/detail
https://jobs.bytedance.com/campus/position/7592475185988290821/detail
https://jobs.bytedance.com/campus/position/7637112157868345605/detail
https://jobs.bytedance.com/campus/position/7559443607137323271/detail
https://jobs.bytedance.com/campus/position/7600339877369366837/detail
https://zhipu-ai.jobs.feishu.cn/zhipucampus/position/7533499097384569130/detail
https://zhipu-ai.jobs.feishu.cn/zhipucampus/position/7514883436093098290/detail
https://zhipu-ai.jobs.feishu.cn/zhipucampus/resume/7561376108466751780
https://app.mokahr.com/campus-recruitment/moonshot/148507?sourceToken=36ae0fe79b871e0600f2b2cfe0a81ebb#/job/646a6e5d-e998-4680-92b0-65b4087c1f7c
https://app.mokahr.com/campus-recruitment/moonshot/148507?sourceToken=36ae0fe79b871e0600f2b2cfe0a81ebb#/job/56c68aef-8f49-4ca9-84d7-f0a581f51fd4
https://xiaomi.jobs.f.mioffice.cn/internship/resume/7621512530250713382/apply?spread=6AA3R7B
https://xiaomi.jobs.f.mioffice.cn/internship/position/7621073873320478995/detail?spread=6AA3R7B
https://xiaomi.jobs.f.mioffice.cn/internship/position/7631479641194613042/detail?spread=6AA3R7B
https://xiaomi.jobs.f.mioffice.cn/internship/position/7646306013033892138/detail?spread=6AA3R7B
https://nio.jobs.feishu.cn/campus/resume/7610630497400113459/
https://nio.jobs.feishu.cn/campus/position/7633337700573186346/detail
https://nio.jobs.feishu.cn/campus/position/7633341626643450155/detail
https://nio.jobs.feishu.cn/campus/position/7633337685424687401/detail
https://campus.163.com/app/detail/index?id=3718&projectId=76
https://hr.163.com/job-detail.html?id=76653
https://hr.163.com/job-detail.html?id=53179&lang=zh
https://hr.163.com/job-detail.html?id=76542&lang=zh
https://hr.163.com/job-detail.html?id=76728&lang=zh
https://hr.sensetime.com/SU60fa3bdabef57c1023fc1cbc/pb/posDetail.html?postId=69c5f53dbe908548be4c6784&postType=intern
https://campus.pingan.com/positionDetail?positionId=925e7df4167a359c89214962e7c67f3e
https://campus.pingan.com/positionDetail?positionId=772c0abbffc13c96bc94cf5359420b57
https://campus.pingan.com/positionDetail?positionId=2f554707b51c3860a2ea28f001b55836
https://campus.pingan.com/positionDetail?positionId=c2d73843b0e83eb38b64e1991bf70cdd
https://zhaopin.meituan.com/web/position/detail?jobUnionId=3524979618&highlightType=campus
https://zhaopin.meituan.com/web/position/detail?jobUnionId=4220518746&highlightType=campus
https://talent.antgroup.com/campus-position?positionId=26030308940392&tid=2196662f17815815948014389e4522
https://01ai.jobs.feishu.cn/index/position/7647157090187905343/detail
https://01ai.jobs.feishu.cn/index/position/7503487638671591717/detail
https://www.lixiang.com/employ/detail/17371.html?jobCode=A02567&fromJob=1
https://campus.kuaishou.cn/recruit/campus/e/#/campus/job-info/11453
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30944
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/29757
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30514
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/27355
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/28315
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/27829
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/28317
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30910
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/29713
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30247
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/29096
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30928
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/28594
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30559
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/26341
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30269
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/30248
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/25937
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/24247
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/27919
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/26995
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/26152
https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/job-info/21452
https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/a0845638-d3e0-45cf-b213-31a7e684c5d4
https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/54f386a9-913b-4626-9bf4-e1709b62fcda
https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/bae90e40-d815-477a-913b-72ca1eeb057b
https://campus.duxiaoman.com/051736/position/7649303782597691694/detail
https://campus.duxiaoman.com/051736/position/7615934023782123803/detail
https://app.mokahr.com/apply/didiglobal/6222#/job/662799be-79e4-45a3-87e8-ecddb6d52658
https://talent.baidu.com/jobs/detail/INTERN/c55378f8-c1b7-4afb-baf3-7fe6a269da9a
https://talent.baidu.com/jobs/detail/INTERN/55ec188b-2518-4b89-b21c-faf4ecf6fa25
https://talent.baidu.com/jobs/detail/INTERN/ec517c43-61ba-4d54-8133-9ab733d85ae9
https://cq6qe6bvfr6.jobs.feishu.cn/baichuanzhaopin/position/7368438041948899621/detail
https://campus-talent.alibaba.com/campus/position/199903240028
https://campus-talent.alibaba.com/campus/position/199904600003?deptCodes=
""".strip().splitlines()

AI_KW = [
    "AI", "人工智能", "大模型", "LLM", "Agent", "AIGC", "多模态", "智能", "NLP", "Prompt", "RAG",
]


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_keywords(title: str, jd: str) -> list:
    s = f"{title}\n{jd}"
    found = [k for k in AI_KW if k.lower() in s.lower()]
    if "产品" in s and "产品" not in found:
        found.append("产品")
    out, seen = [], set()
    for k in found:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:6] or ["AI"]


def infer_type(title: str, jd: str, url: str) -> str:
    s = f"{title} {jd} {url}"
    if re.search(r"实习|intern|trainee|postType=intern", s, re.I):
        return "实习"
    if re.search(r"校招|campus|应届|毕业生", s, re.I):
        return "校招"
    if re.search(r"社招|experienced|social", s, re.I):
        return "社招"
    return "校招"


def make_job(
    job_id: int,
    company: str,
    bu: str,
    title: str,
    city: str,
    hire: str,
    jd: str,
    url: str,
    tags: list | None = None,
) -> dict:
    jd = jd.strip()
    summary = jd[:80].replace("\n", "，") + ("…" if len(jd) > 80 else "")
    return {
        "id": job_id,
        "company": company or OMIT,
        "bu": bu or "—",
        "title": title or "待确认岗位",
        "city": city or OMIT,
        "type": hire or "校招",
        "tags": tags or ["已投递"],
        "jd": jd or f"投递链接：{url}",
        "url": url,
        "summary": summary,
        "keywords": extract_keywords(title, jd),
    }


def fetch_bytedance(job_id: str, url: str) -> dict | None:
    api = f"https://jobs.bytedance.com/api/v1/job/posts/{job_id}"
    r = requests.get(
        api,
        headers={"User-Agent": UA, "Referer": "https://jobs.bytedance.com/"},
        timeout=20,
    )
    if r.status_code != 200:
        return None
    data = r.json().get("data", {}).get("job_post_detail") or {}
    if not data:
        return None
    city = (data.get("city_info") or {}).get("name") or OMIT
    desc = strip_html(data.get("description") or "")
    req = strip_html(data.get("requirement") or "")
    jd = "\n\n".join(x for x in [f"职位描述\n{desc}" if desc else "", f"职位要求\n{req}" if req else ""] if x)
    dept = data.get("department") or data.get("job_category") or {}
    bu = dept.get("name") if isinstance(dept, dict) else str(dept or "字节跳动")
    return {
        "company": "字节跳动",
        "bu": bu or "字节跳动",
        "title": data.get("title") or "",
        "city": city,
        "type": infer_type(data.get("title", ""), jd, url),
        "jd": jd,
    }


def fetch_netease_hr(job_id: str, url: str) -> dict | None:
    api = f"https://hr.163.com/api/hr163/position/query?id={job_id}"
    r = requests.get(api, headers={"User-Agent": UA}, timeout=20)
    if r.status_code != 200:
        return None
    job = r.json().get("data") or {}
    if not job:
        return None
    cities = "、".join(job.get("workPlaceNameList") or []) or OMIT
    bu = job.get("productName") or job.get("firstPostTypeName") or "网易"
    desc = strip_html(job.get("description") or "")
    req = strip_html(job.get("requirement") or "")
    jd = "\n\n".join(x for x in [f"职位描述\n{desc}" if desc else "", f"职位要求\n{req}" if req else ""] if x)
    wt = str(job.get("workType", "0"))
    hire = "实习" if wt == "1" else ("校招" if wt == "2" else "社招")
    return {
        "company": "网易",
        "bu": bu,
        "title": job.get("name") or "",
        "city": cities,
        "type": hire,
        "jd": jd,
    }


def parse_bilibili_detail(data: dict) -> dict:
    d = data.get("data") or data
    title = d.get("positionName") or d.get("name") or ""
    desc = strip_html(d.get("positionDescription") or "")
    req = strip_html(d.get("positionRequirement") or "")
    jd = "\n\n".join(x for x in [f"工作职责\n{desc}" if desc else "", f"工作要求\n{req}" if req else ""] if x)
    loc = d.get("workLocationName") or d.get("workPlace") or OMIT
    dept = d.get("deptName") or d.get("departmentName") or "B站"
    return {
        "company": "B站",
        "bu": dept,
        "title": title,
        "city": loc,
        "type": infer_type(title, jd, ""),
        "jd": jd,
    }


def parse_kuaishou_detail(data: dict) -> dict:
    r = data.get("result") or data.get("data") or data
    title = r.get("name") or r.get("positionName") or ""
    desc = strip_html(r.get("description") or r.get("positionDesc") or "")
    demand = strip_html(r.get("positionDemand") or r.get("requirement") or "")
    jd = "\n\n".join(x for x in [f"岗位职责\n{desc}" if desc else "", f"任职要求\n{demand}" if demand else ""] if x)
    locs = [x.get("name", "") for x in (r.get("workLocationDicts") or []) if isinstance(x, dict)]
    loc = "、".join(locs) or OMIT
    return {
        "company": "快手",
        "bu": r.get("departmentName") or "快手",
        "title": title,
        "city": loc,
        "type": "实习" if "trainee" in title.lower() or "实习" in title else infer_type(title, jd, ""),
        "jd": jd,
    }


def parse_feishu_ats(data: dict, company: str) -> dict | None:
    d = data.get("data") or data.get("result") or data
    if isinstance(d, dict) and "job" in d:
        d = d["job"]
    if not isinstance(d, dict):
        return None
    title = d.get("title") or d.get("name") or d.get("position_name") or ""
    desc = strip_html(d.get("description") or d.get("job_description") or d.get("duty") or "")
    req = strip_html(d.get("requirement") or d.get("job_requirement") or "")
    jd = "\n\n".join(x for x in [desc, req] if x)
    city_list = d.get("city_list") or d.get("city_infos") or []
    if isinstance(city_list, list) and city_list:
        city = "、".join(
            c.get("name") or c.get("city_name") or str(c) for c in city_list if c
        )
    else:
        city = d.get("city") or OMIT
    return {
        "company": company,
        "bu": d.get("department_name") or d.get("department") or company,
        "title": title,
        "city": city if city else OMIT,
        "type": infer_type(title, jd, ""),
        "jd": jd,
    }


def parse_dom_body(body_text: str, url: str) -> dict | None:
    if not body_text or len(body_text) < 40:
        return None
    host = urlparse(url).netloc
    company = OMIT
    for k, v in {
        "vivo.com": "vivo", "oppo.com": "OPPO", "kuaishou": "快手", "mokahr.com": "Moka",
        "meituan.com": "美团", "antgroup.com": "蚂蚁集团", "lixiang.com": "理想汽车",
        "pingan.com": "平安", "sensetime.com": "商汤科技", "baidu.com": "百度",
        "alibaba.com": "淘天/阿里控股", "duxiaoman": "度小满", "01ai.jobs": "零一万物",
        "zhipu": "智谱AI", "xiaomi.jobs": "小米", "nio.jobs": "蔚来", "baichuan": "百川智能",
        "moonshot": "月之暗面", "high-flyer": "幻方量化", "didiglobal": "滴滴",
        "163.com": "网易", "bilibili": "B站",
    }.items():
        if k in host or k in url:
            company = v
            break
    if "moonshot" in url:
        company = "月之暗面"
    if "high-flyer" in url:
        company = "幻方量化"

    lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]
    noise = {
        "首页", "登录", "注册", "分享", "申请职位", "职位列表", "职位详情", "校园招聘", "社会招聘",
        "关于我们", "返回", "投递", "立即申请", "发布于", "日常实习", "校招", "社招",
    }
    title = ""
    for ln in lines:
        if ln in noise or len(ln) < 4:
            continue
        if ln in ("实习生职位", "应届生职位", "宣讲会行程"):
            continue
        if re.match(r"^(职位|工作|更新|发布|Base|实习\||全职\|)", ln):
            continue
        if re.search(r"产品|经理|实习生|工程师|运营|算法", ln) and len(ln) < 80:
            title = ln
            break
    if not title:
        for ln in lines:
            if 6 <= len(ln) <= 60 and ln not in noise:
                title = ln
                break

    jd = ""
    for marker in ("职位描述", "岗位职责", "工作职责", "职位要求", "任职要求"):
        if marker in body_text:
            chunk = body_text.split(marker, 1)[-1]
            for end in ("任职要求", "职位要求", "投递", "申请职位", "立即申请"):
                if end in chunk and end != marker:
                    chunk = chunk.split(end)[0]
            jd = f"{marker}\n" + chunk[:5000].strip()
            break
    if not jd and len(body_text) > 100:
        jd = body_text[:5000]

    city = OMIT
    m = re.search(r"工作地点[：:]\s*([^\n|]+)", body_text)
    if m:
        city = m.group(1).strip()
    else:
        m = re.search(r"(北京|上海|深圳|杭州|广州|成都|南京|武汉|西安|苏州)", body_text)
        if m:
            city = m.group(1)

    if not title:
        return None
    return {
        "company": company,
        "bu": company if company != OMIT else "—",
        "title": title,
        "city": city,
        "type": infer_type(title, jd, url),
        "jd": jd,
    }


def fetch_kuaishou_via_ctx(page, job_id: str) -> dict | None:
    api = f"https://zhaopin.kuaishou.cn/recruit/e/api/v1/open/position?id={job_id}"
    try:
        r = page.context.request.get(
            api,
            headers={"User-Agent": UA, "Referer": "https://zhaopin.kuaishou.cn/recruit/e/"},
        )
        if r.ok:
            body = r.json()
            if body.get("code") == 0:
                parsed = parse_kuaishou_detail(body)
                if parsed.get("title"):
                    return parsed
    except Exception:
        pass
    return None

def scrape_with_playwright(url: str, page=None) -> dict | None:
    captured_json: list[tuple[str, dict]] = []
    own_browser = page is None

    if own_browser:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale="zh-CN")
        page = ctx.new_page()
    else:
        pw = browser = ctx = None

    # 快手：直接调 open API（不依赖 response 监听）
    m = re.search(r"job-info/(\d+)", url)
    if m and "kuaishou" in url:
        got = fetch_kuaishou_via_ctx(page, m.group(1))
        if got:
            if own_browser:
                browser.close()
                pw.stop()
            return got

    def on_resp(resp):
        if resp.status != 200:
            return
        ct = resp.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            body = resp.json()
        except Exception:
            return
        u = resp.url
        if any(k in u for k in ("position", "job", "detail", "query", "open/position", "post")):
            captured_json.append((u, body))

    page.on("response", on_resp)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
    except Exception:
        pass

    # bilibili: after csrf, call detail
    m = re.search(r"/positions/(\d+)", url)
    if m and "bilibili.com" in url:
        pid = m.group(1)
        try:
            csrf = None
            for u, body in captured_json:
                if "csrf/token" in u and body.get("data"):
                    csrf = body["data"]
                    break
            if csrf:
                api = f"https://jobs.bilibili.com/api/campus/position/detail/{pid}"
                r = page.context.request.get(api, headers={"ajSessionId": csrf, "User-Agent": UA})
                if r.ok:
                    captured_json.append((api, r.json()))
        except Exception:
            pass

    body_text = ""
    try:
        body_text = page.inner_text("body")
    except Exception:
        pass

    dom_title = ""
    dom_jd = ""
    try:
        for sel in ["h1", "h2.job-title", ".job-title", "[class*='job-title']", ".position-title"]:
            loc = page.locator(sel)
            if loc.count():
                dom_title = loc.first.inner_text(timeout=2000).strip()
                if dom_title and dom_title not in ("校园招聘", "社会招聘", "首页"):
                    break
    except Exception:
        pass
    if not dom_jd and body_text and "职位描述" in body_text:
        dom_jd = body_text.split("职位描述", 1)[-1][:4000].strip()

    try:
        page.remove_listener("response", on_resp)
    except Exception:
        pass

    if own_browser:
        browser.close()
        pw.stop()

    host = urlparse(url).netloc

    for api_url, body in captured_json:
        if body.get("code") not in (0, 200, "0", None) and body.get("message") not in ("ok", "success", None):
            if body.get("code") == -101:
                continue
        if "bilibili.com" in api_url and "/position/detail" in api_url:
            parsed = parse_bilibili_detail(body)
            if parsed.get("title"):
                return parsed
        if "open/position" in api_url and body.get("code") == 0:
            parsed = parse_kuaishou_detail(body)
            if parsed.get("title"):
                return parsed
        if "hr163/position/query" in api_url:
            job = body.get("data") or {}
            if job.get("name"):
                return fetch_netease_hr(str(job.get("id")), url)

    # feishu ATS patterns in captured responses
    company_map = {
        "xiaomi.jobs": "小米",
        "nio.jobs": "蔚来",
        "zhipu-ai.jobs": "智谱AI",
        "01ai.jobs": "零一万物",
        "baichuan": "百川智能",
        "duxiaoman": "度小满",
    }
    co = "未知"
    for k, v in company_map.items():
        if k in host:
            co = v
            break
    for _, body in captured_json:
        parsed = parse_feishu_ats(body, co)
        if parsed and parsed.get("title"):
            return parsed

    # mokahr DOM
    if "mokahr.com" in host and dom_title:
        co = "月之暗面" if "moonshot" in url else ("幻方量化" if "high-flyer" in url else ("滴滴" if "didiglobal" in url else "Moka招聘"))
        return {
            "company": co,
            "bu": co,
            "title": dom_title,
            "city": OMIT,
            "type": infer_type(dom_title, dom_jd, url),
            "jd": dom_jd or f"投递链接：{url}",
        }

    dom_parsed = parse_dom_body(body_text, url)
    if dom_parsed:
        return dom_parsed

    if dom_title:
        return {
            "company": co if co != "未知" else OMIT,
            "bu": "—",
            "title": dom_title,
            "city": OMIT,
            "type": infer_type(dom_title, dom_jd, url),
            "jd": dom_jd or f"投递链接：{url}",
        }

    return None


def scrape_url(url: str, page=None) -> dict | None:
    url = url.strip()
    if not url:
        return None

    m = re.search(r"/campus/position/(\d+)/detail", url)
    if m and "bytedance.com" in url:
        return fetch_bytedance(m.group(1), url)

    m = re.search(r"job-detail\.html\?id=(\d+)", url)
    if m and "hr.163.com" in url:
        return fetch_netease_hr(m.group(1), url)

    return scrape_with_playwright(url, page=page)


def dedupe_urls(urls: list[str]) -> list[str]:
    seen, out = set(), []
    for u in urls:
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def main():
    urls = dedupe_urls(URLS)
    print(f"共 {len(urls)} 条唯一链接待抓取", flush=True)

    jobs_out = []
    report = []
    next_id = START_ID

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="zh-CN")
    page = ctx.new_page()

    # 预热快手会话（否则 open/position API 会失败）
    try:
        page.goto("https://zhaopin.kuaishou.cn/recruit/e/#/official/trainee/", timeout=45000)
        page.wait_for_timeout(2000)
    except Exception:
        pass

    try:
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url[:70]}…", flush=True)
            meta = None
            err = None
            try:
                meta = scrape_url(url, page=page)
            except Exception as e:
                err = str(e)
            if meta and meta.get("title"):
                job = make_job(
                    next_id,
                    company=meta["company"],
                    bu=meta["bu"],
                    title=meta["title"],
                    city=meta["city"],
                    hire=meta["type"],
                    jd=meta["jd"],
                    url=url,
                )
                jobs_out.append(job)
                report.append({"url": url, "status": "ok", "id": next_id, "title": meta["title"]})
                next_id += 1
            else:
                slug = hashlib.md5(url.encode()).hexdigest()[:8]
                job = make_job(
                    next_id,
                    company=OMIT,
                    bu="抓取失败",
                    title=f"待补全岗位（{slug}）",
                    city=OMIT,
                    hire=infer_type("", "", url),
                    jd=f"自动抓取未拿到完整 JD，请人工补全。\n\n投递链接：\n{url}",
                    url=url,
                )
                jobs_out.append(job)
                report.append({"url": url, "status": "fallback", "id": next_id, "error": err})
                next_id += 1
            time.sleep(0.15)
    finally:
        browser.close()
        pw.stop()

    seed_apply = {str(j["id"]): "applied" for j in jobs_out}
    manifest = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "真实抓取的已投递岗位（extraJobs）；不修改 jobs.json；baseline 保持原 86 条不变",
        "total": len(jobs_out),
        "seedApply": seed_apply,
        "extraJobs": jobs_out,
        "entries": report,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for r in report if r["status"] == "ok")
    print(f"\n完成：成功 {ok}/{len(urls)}，写入 {MANIFEST}", flush=True)


if __name__ == "__main__":
    main()
