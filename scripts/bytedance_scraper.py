"""
字节跳动招聘 AI 产品经理岗位采集脚本
API: POST https://jobs.bytedance.com/api/v1/search/job/posts
portal_type: 1=社招, 2=校招, 3=实习
分页: offset（不是页码）
输出：
  bytedance_jobs.json
  bytedance_audit.json
"""

import requests
import json
import time
import random
from datetime import datetime

# ── 配置 ──────────────────────────────────────────────────────
API_URL = "https://jobs.bytedance.com/api/v1/search/job/posts"
LIMIT = 50
MAX_RETRIES = 3
RETRY_DELAY = 5

# 产品类岗位 category ID（从你的 payload 提取）
JOB_CATEGORY_IDS = [
    "6704215864629004552",
    "6704215864591255820",
    "6704216224387041544",
    "6704215924712409352",
]

# 三种招聘类型
PORTAL_TYPES = [
    (2, "社招"),
    (1, "校招"),
    (3, "实习"),
]

KEYWORDS = [
    "AI产品经理",
    "产品经理",
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

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://jobs.bytedance.com/",
    "Origin": "https://jobs.bytedance.com",
}


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
    if "评测平台" in title:
        return True, f"评测平台技术岗：{title}"
    if "智能硬件" in title:
        return True, f"硬件产品岗：{title}"
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


# ── 采集 ──────────────────────────────────────────────────────

def fetch_single_page(keyword: str, portal_type: int, offset: int):
    payload = {
        "keyword": keyword,
        "limit": LIMIT,
        "offset": offset,
        "job_category_id_list": JOB_CATEGORY_IDS,
        "tag_id_list": [],
        "location_code_list": [],
        "subject_id_list": [],
        "recruitment_id_list": [],
        "portal_type": portal_type,
        "job_function_id_list": [],
        "storefront_id_list": [],
        "portal_entrance": 1,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(API_URL, json=payload, headers=HEADERS, timeout=15)
            if resp.status_code == 429:
                time.sleep(RETRY_DELAY * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                print(f" ⚠️API异常:code={data.get('code')}", end="", flush=True)
                return None, None
            page_data = data.get("data") or {}
            jobs = page_data.get("job_post_list") or []
            has_more = page_data.get("has_more", False)
            total = page_data.get("count", 0)
            return jobs, (has_more, total)
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                print(f" ❌{type(e).__name__}", end="", flush=True)
                return None, None
    return None, None


def fetch_by_keyword(keyword: str, portal_type: int, type_label: str) -> list:
    all_jobs = []
    offset = 0
    print(f"  [{type_label}] 「{keyword}」", end="", flush=True)

    while True:
        jobs, meta = fetch_single_page(keyword, portal_type, offset)
        if jobs is None:
            print(f" ⚠️跳过", end="", flush=True)
            break
        has_more, total = meta
        if offset == 0:
            if total == 0:
                print(f" →无结果", end="", flush=True)
                break
            print(f" →共{total}条", end="", flush=True)
        all_jobs.extend(jobs)
        page_num = offset // LIMIT + 1
        print(f" [{page_num}✓]", end="", flush=True)
        if not has_more or not jobs:
            break
        offset += LIMIT
        time.sleep(random.uniform(1.0, 1.8))

    print()
    return all_jobs


def deduplicate(jobs: list) -> list:
    seen = set()
    result = []
    for j in jobs:
        uid = str(j.get("id", ""))
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
            print(f"  ⚠️ 已写入备份")
        except Exception:
            print(f"  ❌ 备份也失败")


# ── 主函数 ────────────────────────────────────────────────────

def main():
    start = datetime.now()
    print("=" * 60)
    print(f"字节跳动招聘采集  {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: 采集
    print("\n[Step 1] 采集")
    all_raw = []
    for portal_type, type_label in PORTAL_TYPES:
        print(f"\n  ▶ {type_label}")
        for keyword in KEYWORDS:
            try:
                jobs = fetch_by_keyword(keyword, portal_type, type_label)
                for j in jobs:
                    j["_type_label"] = type_label
                all_raw.extend(jobs)
            except Exception as e:
                print(f"\n  ❌「{keyword}」异常: {e}")

    print(f"\n  原始: {len(all_raw)} 条")
    if not all_raw:
        print("  ⚠️ 未采集到数据")
        return

    # Step 2: 去重
    unique = deduplicate(all_raw)
    print(f"  去重后: {len(unique)} 条")

    # Step 3: 筛选
    print("\n[Step 2] 筛选")
    audit_log = []
    kept = []

    for job in unique:
        title = job.get("title", "")
        jd = "\n".join(filter(None, [job.get("description"), job.get("requirement")]))
        recruit_type = job.get("recruit_type") or {}
        type_name = recruit_type.get("name", "")
        parent_name = (recruit_type.get("parent") or {}).get("name", "")
        if "实习" in type_name:
            type_label = "实习"
        elif "校招" in parent_name or "校招" in type_name:
            type_label = "校招"
        else:
            type_label = "社招"
        job_id = job.get("id", "")
        # 详情页 URL 规则
        if type_label == "社招":
            url = f"https://jobs.bytedance.com/experienced/position/{job_id}"
        else:
            url = f"https://jobs.bytedance.com/campus/position/{job_id}"

        def audit(decision, stage, reason):
            audit_log.append({
                "decision": decision, "stage": stage, "reason": reason,
                "title": title, "type": type_label, "url": url,
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
            kept.append({"job": job, "jd": jd, "url": url, "type_label": type_label})
        except Exception as e:
            audit("KEPT_BY_ERROR", "筛选异常", str(e))
            kept.append({"job": job, "jd": jd, "url": url, "type_label": type_label})

    cnt = lambda d: sum(1 for r in audit_log if r["decision"] == d)
    print(f"  硬排除:     {cnt('EXCLUDED_HARD'):>3} 条")
    print(f"  技术门槛高: {cnt('EXCLUDED_TECH'):>3} 条")
    print(f"  ✅ 保留:     {cnt('KEPT'):>3} 条")

    # Step 4: 格式化
    formatted = []
    for i, item in enumerate(kept):
        job = item["job"]
        jd = item["jd"]
        title = job.get("title", "")
        try:
            ai_type = classify_ai_type(title, jd)
            type_label = item["type_label"]
            cats = [ai_type]
            if any(k in title for k in ["高级", "资深", "高阶", "Senior"]):
                cats.append("高级")
            elif type_label == "实习" or "实习" in title:
                cats.append("实习生")
            else:
                cats.append("中级")
            city = (job.get("city_info") or {}).get("name", "")
            # 多城市
            city_list = [c.get("name", "") for c in (job.get("city_list") or [])]
            if city_list:
                city = "、".join(city_list)
            summary = jd[:60].strip().replace("\n", "，") + "..." if len(jd) > 60 else jd
            keywords = list(set([kw for kw in AI_TITLE_KEYWORDS if kw in jd or kw in title]))[:6]
            formatted.append({
                "id": i + 1,
                "company": "字节跳动",
                "bu": "",
                "title": title,
                "city": city,
                "type": type_label,
                "cats": cats,
                "url": item["url"],
                "jd": jd,
                "summary": summary,
                "keywords": keywords,
            })
        except Exception as e:
            formatted.append({
                "id": i + 1, "company": "字节跳动",
                "title": title, "url": item["url"],
                "_format_error": str(e),
            })

    type_order = {"实习": 0, "校招": 1, "社招": 2}
    formatted.sort(key=lambda x: type_order.get(x.get("type", ""), 3))

    # Step 5: 写文件
    print("\n[Step 3] 写入文件")
    safe_write("bytedance_jobs.json", formatted, f"{len(formatted)} 条")
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
    safe_write("bytedance_audit.json", audit_out, f"{len(audit_log)} 条决策记录")

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
