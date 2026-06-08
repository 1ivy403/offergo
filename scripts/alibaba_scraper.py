"""
阿里巴巴招聘 AI 产品经理岗位采集脚本
覆盖：
  1. talent-holding.alibaba.com — 控股集团社招
  2. campus-talent.alibaba.com  — 校招/实习（含淘天、闪购、飞猪等）
输出：
  alibaba_jobs.json    最终保留岗位
  alibaba_audit.json   完整决策留痕
"""

import requests
import json
import re
import time
import random
from datetime import datetime
from pathlib import Path

from alibaba_auth import get_campus_session, get_social_session
from config import JOBS_DIR

SESSION_COOKIES: dict = {}

# ── 配置 ──────────────────────────────────────────────────────
MAX_PAGES = 100
MAX_RETRIES = 3
RETRY_DELAY = 5
PAGE_SIZE = 20

SOCIAL_BASE = "https://talent-holding.alibaba.com"
CAMPUS_BASE = "https://campus-talent.alibaba.com"

# 社招接口（实际 URL 在 main 里拼入 CSRF token）
SOCIAL_API = "https://talent-holding.alibaba.com/position/search"

# 校招/实习接口（多个事业群共用同一接口，靠 customDeptCode 区分）
CAMPUS_API = "https://campus-talent.alibaba.com/position/search"
CAMPUS_BATCH_ID = 100000540002  # 阿里巴巴2027届实习生

# 校招事业群配置（name, customDeptCode）
# customDeptCode 为空则不传该字段（默认全量）
CAMPUS_GROUPS = [
    ("淘天集团", "FZAWD2,ISF5EF,GT6VEB,VPZR2I,IU58EO,TOJKBE,82HV2J,Y948F9,9ZXLJA,LWEPZA,FNXWXJ,FAIKKZ,T8B7EB,KZ3MMU,PV2VJW,SVOKFU,XUYVTV,C7JZI6,3AAN8G,ST33QY"),
    ("淘宝闪购", "WMREYL,VKQ50F,HEVDFW,FJNV9M"),
    ("飞猪", ""),  # 无 customDeptCode，默认全量
    ("全集团", ""),  # 兜底：不限事业群搜一遍
]

KEYWORDS = [
    "产品经理",
    "AI产品",
    "大模型产品",
    "Agent产品",
    "智能产品",
]

AI_TITLE_KEYWORDS = [
    "AI", "大模型", "Agent", "LLM", "智能", "AIGC",
    "人工智能", "机器学习", "Prompt", "向量", "多模态",
]

HIGH_TECH_SIGNALS = [
    ("预训练", 3), ("RLHF", 3), ("SFT", 3), ("模型蒸馏", 3),
    ("模型训练", 3), ("Fine-tuning", 3), ("算力调度", 3),
    ("GPU资源", 3), ("CUDA", 3), ("推理优化", 3), ("MLOps", 3),
    ("IaaS", 3), ("PaaS", 2), ("底层架构", 2), ("基础设施", 2),
    ("分布式训练", 2), ("模型压缩", 2), ("代码能力", 2), ("编程能力", 2),
]

PRODUCT_SIGNALS = [
    "产品规划", "用户体验", "业务场景", "商业化", "路线图",
    "产品迭代", "需求分析", "用户调研", "产品设计",
    "Prompt Engineering", "Agent应用", "落地", "产品化", "产品经理",
]

HEADERS_SOCIAL = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://talent-holding.alibaba.com/",
    "Origin": "https://talent-holding.alibaba.com",
}

HEADERS_CAMPUS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://campus-talent.alibaba.com/",
    "Origin": "https://campus-talent.alibaba.com",
}


# ── CSRF Token ────────────────────────────────────────────────

def get_csrf_token(base_url: str, headers: dict) -> str:
    """访问首页获取 CSRF token；失败时用 Playwright 拉 session。"""
    try:
        resp = requests.get(base_url, headers=headers, timeout=15)
        csrf = resp.cookies.get("_csrf") or resp.cookies.get("csrf_token")
        if csrf:
            return csrf
        match = re.search(r'_csrf["\s:=]+([a-f0-9\-]{30,})', resp.text)
        if match:
            return match.group(1)
        csrf = resp.headers.get("X-CSRF-Token") or resp.headers.get("csrf-token")
        if csrf:
            return csrf
    except Exception as e:
        print(f"  ⚠️ 获取CSRF失败: {e}")

    global SESSION_COOKIES
    if base_url.rstrip("/") == SOCIAL_BASE:
        csrf, cookies = get_social_session()
    else:
        csrf, cookies = get_campus_session()
    if cookies:
        SESSION_COOKIES.update(cookies)
    if csrf:
        print(f"  ✅ Playwright 获取 CSRF: {csrf[:20]}...")
    return csrf or ""


# ── 筛选逻辑 ──────────────────────────────────────────────────

def hard_exclude(title: str):
    if not any(kw in title for kw in ["产品", "PM", "Product"]):
        return True, f"标题不含产品相关词：{title}"
    for kw in ["算法工程师", "研发工程师", "后端工程师", "前端工程师",
               "数据工程师", "测试工程师", "运维工程师",
               "市场", "销售", "财务", "法务", "HR", "招聘"]:
        if kw in title:
            return True, f"标题含排除词「{kw}」"
    if "经理" in title and "产品" not in title:
        return True, f"含'经理'但非产品经理：{title}"
    if "工程师" in title:
        return True, f"工程师岗：{title}"
    if "运营" in title and "产品经理" not in title:
        return True, f"运营岗：{title}"
    if any(kw in title for kw in ["设计师", "视觉设计", "UX"]):
        return True, f"设计岗：{title}"
    if "前端" in title:
        return True, f"前端岗：{title}"
    if "开发" in title and "产品经理" not in title:
        return True, f"开发岗：{title}"
    if "数据分析师" in title:
        return True, f"数据分析师岗：{title}"
    if "Product Engineer" in title or "产品工程师" in title:
        return True, f"工程师岗：{title}"
    if not any(kw in title for kw in AI_TITLE_KEYWORDS):
        return True, f"标题不含AI相关词：{title}"
    return False, ""


def calc_tech_score(jd_text: str):
    tech_score = 0
    triggered = []
    for kw, score in HIGH_TECH_SIGNALS:
        if kw.lower() in jd_text.lower():
            tech_score += score
            triggered.append(f"{kw}(+{score})")
    product_score = sum(1 for kw in PRODUCT_SIGNALS if kw.lower() in jd_text.lower())
    return tech_score, triggered, product_score


def should_exclude_by_tech(jd_text: str):
    tech_score, signals, product_score = calc_tech_score(jd_text)
    detail = f"tech_score={tech_score}, 信号词={signals}, product_score={product_score}"
    if tech_score >= 5 and product_score <= 1:
        return True, f"技术门槛过高 | {detail}"
    if tech_score >= 7:
        return True, f"极高技术门槛 | {detail}"
    return False, f"技术门槛可接受 | {detail}"


def classify_ai_type(title: str, jd: str) -> str:
    text = title + " " + jd
    if any(kw in text for kw in ["Agent产品", "AI原生", "AIGC产品", "大模型应用", "多模态产品"]):
        return "AI原生"
    if any(kw in text for kw in ["模型产品", "评测产品", "训练数据", "模型能力"]):
        return "AI模型产品"
    if any(kw in text for kw in ["基础架构", "基础设施", "PaaS", "IaaS", "算力", "MLOps"]):
        return "AI平台架构"
    return "AI赋能"


# ── 请求工具 ──────────────────────────────────────────────────

def post_with_retry(url, payload, headers):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url, json=payload, headers=headers,
                cookies=SESSION_COOKIES or None, timeout=15,
            )
            if resp.status_code == 403:
                print(f"  ❌403(CSRF/会话失效)", end="", flush=True)
                return None
            if resp.status_code == 429:
                time.sleep(RETRY_DELAY * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.exceptions.RequestException:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except Exception:
            return None
    return None


# ── 社招采集 ──────────────────────────────────────────────────

def fetch_social(keyword: str) -> list:
    all_jobs = []
    page = 1
    print(f"  [社招] 「{keyword}」", end="", flush=True)

    while page <= MAX_PAGES:
        payload = {
            "channel": "group_official_site",
            "language": "zh",
            "batchId": "",
            "categories": "",
            "deptCodes": [],
            "key": keyword,
            "myReferralShareCode": "",
            "pageIndex": page,
            "pageSize": PAGE_SIZE,
            "regions": "",
            "shareId": "",
            "shareType": "",
            "subCategories": "",
        }

        data = post_with_retry(SOCIAL_API, payload, HEADERS_SOCIAL)
        if not data or not data.get("success"):
            print(f" ⚠️API异常", end="", flush=True)
            break

        content = data.get("content") or {}
        jobs = content.get("datas") or []
        total = content.get("total") or 0
        total_pages = -(-total // PAGE_SIZE)  # 向上取整

        if page == 1:
            if not jobs:
                print(f" →无结果", end="", flush=True)
                break
            print(f" →共{total}条/{total_pages}页", end="", flush=True)

        all_jobs.extend(jobs)
        print(f" [{page}✓]", end="", flush=True)

        if not jobs or page >= total_pages:
            break

        page += 1
        time.sleep(random.uniform(1.0, 1.8))

    print()
    return all_jobs


# ── 校招/实习采集 ─────────────────────────────────────────────

def fetch_campus(keyword: str, group_name: str, custom_dept_code: str) -> list:
    all_jobs = []
    page = 1
    dept_label = f"({group_name})" if group_name else ""
    print(f"  [实习{dept_label}] 「{keyword}」", end="", flush=True)

    while page <= MAX_PAGES:
        payload = {
            "batchId": CAMPUS_BATCH_ID,
            "searchKey": keyword,
            "pageIndex": page,
            "pageSize": PAGE_SIZE,
            "channel": "campus_group_official_site",
            "language": "zh",
        }
        if custom_dept_code:
            payload["customDeptCode"] = custom_dept_code

        data = post_with_retry(CAMPUS_API, payload, HEADERS_CAMPUS)
        if not data or not data.get("success"):
            print(f" ⚠️API异常", end="", flush=True)
            break

        content = data.get("content") or {}
        jobs = content.get("datas") or []
        total = content.get("total")
        if total:
            total_pages = max(-(-total // PAGE_SIZE), 1)
        elif jobs:
            total_pages = MAX_PAGES
        else:
            total_pages = 0

        if page == 1:
            if not jobs:
                print(f" →无结果", end="", flush=True)
                break
            label = f"→共{total}条/{total_pages}页" if total else f"→{len(jobs)}条/未知页数"
            print(f" {label}", end="", flush=True)

        all_jobs.extend(jobs)
        print(f" [{page}✓]", end="", flush=True)

        if not jobs or page >= total_pages:
            break

        page += 1
        time.sleep(random.uniform(1.0, 1.8))

    print()
    return all_jobs


# ── 归一化 ────────────────────────────────────────────────────

def normalize_social(job: dict) -> dict:
    jd = "\n".join(filter(None, [job.get("description"), job.get("requirement")]))
    return {
        "_id": str(job.get("id", "")),
        "_source": "social",
        "title": job.get("name", ""),
        "city": "、".join(job.get("workLocations") or []),
        "bu": "",
        "type": "社招",
        "jd": jd,
        "url": f"https://talent-holding.alibaba.com/off-campus/position-detail?positionId={job.get('id')}",
    }


def normalize_campus(job: dict) -> dict:
    jd = "\n".join(filter(None, [job.get("description"), job.get("requirement")]))
    circles = "、".join(job.get("circleNames") or [])
    pos_url = job.get("positionUrl") or ""
    if pos_url and not pos_url.startswith("http"):
        pos_url = f"https://campus-talent.alibaba.com{pos_url}"
    elif not pos_url:
        pos_url = f"https://campus-talent.alibaba.com/campus/position-detail?positionId={job.get('id')}"

    return {
        "_id": str(job.get("id", "")),
        "_source": "campus",
        "title": job.get("name", ""),
        "city": "、".join(job.get("workLocations") or []),
        "bu": circles,
        "type": "实习",
        "jd": jd,
        "url": pos_url,
    }


def deduplicate(jobs: list) -> list:
    seen = set()
    result = []
    for j in jobs:
        uid = j.get("_id")
        if uid and uid not in seen:
            seen.add(uid)
            result.append(j)
    return result


def safe_write(path, data, label):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ {path} ({label})")
    except OSError as e:
        print(f"  ❌ 写入失败: {e}")
        try:
            with open(f"backup_{path}", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  ⚠️ 已写入备份文件")
        except Exception:
            print(f"  ❌ 备份也失败")


# ── 主函数 ────────────────────────────────────────────────────

def main():
    start = datetime.now()
    print("=" * 60)
    print(f"阿里巴巴招聘采集  {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 0: 获取 CSRF Token
    print("\n[Step 0] 获取 CSRF Token")
    global SOCIAL_API, CAMPUS_API
    social_csrf = get_csrf_token(SOCIAL_BASE, HEADERS_SOCIAL)
    campus_csrf = get_csrf_token(CAMPUS_BASE, HEADERS_CAMPUS)
    print(f"  社招 CSRF: {social_csrf[:20] if social_csrf else '未获取到'}...")
    print(f"  校招 CSRF: {campus_csrf[:20] if campus_csrf else '未获取到'}...")
    if social_csrf:
        SOCIAL_API = f"{SOCIAL_BASE}/position/search?_csrf={social_csrf}"
    if campus_csrf:
        CAMPUS_API = f"{CAMPUS_BASE}/position/search?_csrf={campus_csrf}"

    all_raw = []

    # Step 1: 社招
    print("\n[Step 1] 控股集团社招")
    for keyword in KEYWORDS:
        try:
            jobs = fetch_social(keyword)
            for j in jobs:
                all_raw.append(normalize_social(j))
        except Exception as e:
            print(f"\n  ❌「{keyword}」异常: {e}")

    # Step 2: 校招/实习（各事业群）
    print("\n[Step 2] 校招/实习")
    seen_campus_ids = set()
    for group_name, dept_code in CAMPUS_GROUPS:
        print(f"\n  ▶ {group_name}")
        for keyword in KEYWORDS:
            try:
                jobs = fetch_campus(keyword, group_name, dept_code)
                for j in jobs:
                    norm = normalize_campus(j)
                    # 同一岗位可能出现在多个事业群，这里先都收集，后面统一去重
                    all_raw.append(norm)
            except Exception as e:
                print(f"\n  ❌「{keyword}」异常: {e}")

    print(f"\n  原始: {len(all_raw)} 条")

    if not all_raw:
        print("  ⚠️ 未采集到数据")
        return

    # Step 3: 去重
    unique = deduplicate(all_raw)
    print(f"  去重后: {len(unique)} 条")

    # Step 4: 筛选
    print("\n[Step 3] 筛选")
    audit_log = []
    kept = []

    for job in unique:
        title = job.get("title", "")
        jd = job.get("jd", "") or ""
        url = job.get("url", "")
        job_type = job.get("type", "")

        def audit(decision, stage, reason):
            audit_log.append({
                "decision": decision, "stage": stage, "reason": reason,
                "title": title, "type": job_type, "url": url,
            })

        try:
            excluded, reason = hard_exclude(title)
            if excluded:
                audit("EXCLUDED_HARD", "硬排除", reason)
                continue

            too_tech, tech_reason = should_exclude_by_tech(title + " " + jd)
            if too_tech:
                audit("EXCLUDED_TECH", "技术门槛", tech_reason)
                continue

            audit("KEPT", "全部通过", tech_reason)
            kept.append(job)

        except Exception as e:
            audit("KEPT_BY_ERROR", "筛选异常", str(e))
            kept.append(job)

    cnt = lambda d: sum(1 for r in audit_log if r["decision"] == d)
    print(f"  硬排除:     {cnt('EXCLUDED_HARD'):>3} 条")
    print(f"  技术门槛高: {cnt('EXCLUDED_TECH'):>3} 条")
    print(f"  ✅ 保留:     {cnt('KEPT'):>3} 条")

    # Step 5: 格式化
    print("\n[Step 4] 格式化")
    formatted = []
    for i, job in enumerate(kept):
        try:
            title = job.get("title", "")
            jd = job.get("jd", "") or ""
            ai_type = classify_ai_type(title, jd)
            cats = [ai_type]
            if any(k in title for k in ["高级", "资深", "高阶", "Senior"]):
                cats.append("高级")
            elif job.get("type") == "实习" or "实习" in title:
                cats.append("实习生")
            else:
                cats.append("中级")

            duty = jd[:60].strip().replace("\n", "，") + "..." if len(jd) > 60 else jd
            keywords = list(set([kw for kw in AI_TITLE_KEYWORDS if kw in jd or kw in title]))[:6]

            formatted.append({
                "id": i + 1,
                "company": "阿里巴巴",
                "bu": job.get("bu", ""),
                "title": title,
                "city": job.get("city", ""),
                "type": job.get("type", ""),
                "cats": cats,
                "url": job.get("url", ""),
                "jd": jd,
                "summary": duty,
                "keywords": keywords,
            })
        except Exception as e:
            formatted.append({
                "id": i + 1, "company": "阿里巴巴",
                "title": job.get("title", ""), "url": job.get("url", ""),
                "_format_error": str(e),
            })

    type_order = {"实习": 0, "校招": 1, "社招": 2}
    formatted.sort(key=lambda x: type_order.get(x.get("type", ""), 3))

    # Step 6: 写文件
    print("\n[Step 5] 写入文件")
    safe_write(str(JOBS_DIR / "alibaba_jobs.json"), formatted, f"{len(formatted)} 条最终岗位")

    audit_out = {
        "_meta": {
            "generated_at": start.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": round((datetime.now() - start).total_seconds(), 1),
            "total_raw": len(all_raw), "after_dedup": len(unique),
            "kept": cnt("KEPT"), "excluded_hard": cnt("EXCLUDED_HARD"),
            "excluded_tech": cnt("EXCLUDED_TECH"),
        },
        "KEPT": [r for r in audit_log if r["decision"] == "KEPT"],
        "EXCLUDED_TECH": [r for r in audit_log if r["decision"] == "EXCLUDED_TECH"],
        "EXCLUDED_HARD": [r for r in audit_log if r["decision"] == "EXCLUDED_HARD"],
    }
    safe_write(str(JOBS_DIR / "alibaba_audit.json"), audit_out, f"{len(audit_log)} 条决策记录")

    print(f"\n{'='*60}")
    print(f"完成  耗时 {round((datetime.now()-start).total_seconds(),1)}s")
    for t in ["实习", "校招", "社招"]:
        print(f"  {t}: {sum(1 for j in formatted if j.get('type')==t)} 条")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n❌ 未预期错误: {e}")
        raise
