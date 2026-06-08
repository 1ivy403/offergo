"""
小红书招聘 AI 产品经理岗位采集脚本（改编自美团版本）
输出：
  xiaohongshu_jobs.json   最终保留岗位（干净结果）
  xiaohongshu_audit.json  完整决策留痕
"""

import requests
import json
import time
import random
from datetime import datetime

# ── 配置 ──────────────────────────────────────────────────────
API_URL = "https://job.xiaohongshu.com/websiterecruit/position/pageQueryPosition"
PAGE_SIZE = 10         # 小红书接口每页10条
MAX_PAGES = 200        # 兜底：单个关键词最多翻200页，防异常死循环
MAX_RETRIES = 3        # 单页请求最多重试次数
RETRY_DELAY = 5        # 重试等待秒数

KEYWORDS = [
    "AI产品经理",
    "产品经理",
    "大模型产品",
    "Agent产品",
    "智能产品经理",
]

AI_KEYWORDS = [
    "AI", "人工智能", "大模型", "LLM", "Agent", "智能",
    "NLP", "多模态", "机器学习", "深度学习", "GPT", "向量",
    "知识图谱", "语音识别", "视觉", "AIGC", "Prompt",
]

HARD_EXCLUDE_TITLE = [
    "算法工程师", "研发工程师", "后端工程师", "前端工程师",
    "数据工程师", "测试工程师", "运维工程师",
    "市场", "销售", "财务", "法务", "HR", "招聘",
    "产品运营", "视觉设计师",
]

HIGH_TECH_SIGNALS = [
    ("预训练", 3), ("RLHF", 3), ("SFT", 3), ("模型蒸馏", 3),
    ("模型训练", 3), ("Fine-tuning", 3), ("fine tuning", 3),
    ("算力调度", 3), ("GPU资源", 3), ("CUDA", 3),
    ("推理优化", 3), ("MLOps", 3), ("IaaS", 3),
    ("PaaS", 2), ("底层架构", 2), ("基础设施", 2),
    ("分布式训练", 2), ("模型压缩", 2),
    ("计算机科学背景", 2), ("算法背景", 2),
    ("代码能力", 2), ("编程能力", 2),
]

PRODUCT_SIGNALS = [
    "产品规划", "用户体验", "业务场景", "商业化", "路线图",
    "产品迭代", "需求分析", "用户调研", "产品设计",
    "Prompt Engineering", "prompt engineering",
    "Agent应用", "落地", "产品化", "产品经理",
]

# social=社招，campus=校招+实习（两者会重叠，去重时用 positionId）
RECRUIT_TYPE_MAP = {"social": "社招", "campus": "校招/实习"}

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


# ── 招聘类型推断 ───────────────────────────────────────────────

def infer_job_type(job: dict) -> str:
    """根据请求时注入的 _recruit_type 和岗位名称推断实际类型"""
    recruit_type = job.get("_recruit_type", "social")
    if recruit_type == "social":
        return "社招"
    name = job.get("positionName", "")
    project = job.get("jobProjectName", "") or ""
    if "实习" in name:
        return "实习"
    if "校招" in name or "校招" in project or "校园" in project:
        return "校招"
    return "校招"


# ── 筛选逻辑 ──────────────────────────────────────────────────

def hard_exclude(job: dict):
    title = job.get("positionName", "")
    # 新增：标题必须含产品相关词，否则直接排除
    PRODUCT_TITLE_MUST = ["产品", "PM", "Product Manager"]
    if not any(kw in title for kw in PRODUCT_TITLE_MUST):
        return True, f"标题不含产品相关词（当前标题：{title}）"
    for kw in HARD_EXCLUDE_TITLE:
        if kw in title:
            return True, f"标题含排除词「{kw}」"
    # 规则4：运营岗排除
    if title.endswith("运营") or title.endswith("运营实习生"):
        return True, f"运营岗：{title}"
    if "产品运营" in title and "产品经理" not in title:
        return True, f"产品运营岗：{title}"
    # 规则5：设计岗排除
    if any(kw in title for kw in ["设计师", "视觉设计", "UX"]):
        return True, f"设计岗：{title}"
    # 规则6：明确非AI产品岗
    if any(kw in title for kw in ["弱电", "TPM", "技术项目经理", "装备产品"]):
        return True, f"非目标岗位：{title}"
    # 规则：排除视觉/图像识别类技术产品岗
    if any(kw in title for kw in ["视觉AI识别", "图像识别产品", "视觉识别"]):
        return True, f"视觉算法技术岗：{title}"
    # 规则：排除运营岗（标题含"运营"且不含"产品经理"）
    if "运营" in title and "产品经理" not in title:
        return True, f"运营岗：{title}"
    # 规则：排除智能硬件产品经理
    if "智能硬件" in title:
        return True, f"硬件产品岗：{title}"
    # 规则7：开发岗排除
    if "前端" in title:
        return True, f"前端岗：{title}"
    if "开发" in title and "产品经理" not in title:
        return True, f"开发岗：{title}"
    # 排除评测平台类产品经理（偏技术平台）
    if "评测平台" in title:
        return True, f"评测平台技术岗：{title}"
    # 排除AI评测/评测平台类（技术偏向）
    if "评测" in title and "产品经理" not in title:
        return True, f"评测技术岗：{title}"
    # 排除含AI探索但主业非AI的岗位
    if "含AI探索" in title:
        return True, f"AI非主业岗：{title}"
    # 排除数据分析师
    if "数据分析师" in title:
        return True, f"数据分析师岗：{title}"
    # 排除工程师岗（补漏）
    if "Product Engineer" in title or "产品工程师" in title:
        return True, f"工程师岗：{title}"
    # 排除语料/数据类产品
    if any(kw in title for kw in ["语料", "数据产品经理"]):
        return True, f"数据/语料技术岗：{title}"
    # 规则8：标题必须含AI相关词，否则排除
    AI_TITLE_KEYWORDS = [
        "AI", "大模型", "Agent", "LLM", "智能", "AIGC",
        "人工智能", "机器学习", "Prompt", "向量", "多模态"
    ]
    if not any(kw in title for kw in AI_TITLE_KEYWORDS):
        return True, f"标题不含AI相关词：{title}"
    return False, ""


def is_ai_related(job: dict):
    labels_text = " ".join(job.get("labels") or []) if job.get("labels") else ""
    text = " ".join([
        job.get("positionName", ""),
        job.get("duty", "") or "",
        labels_text,
    ])
    hit = [kw for kw in AI_KEYWORDS if kw.lower() in text.lower()]
    if hit:
        return True, f"命中AI关键词: {hit}"
    return False, "未命中任何AI关键词"


def calc_tech_score(job: dict):
    labels_text = " ".join(job.get("labels") or []) if job.get("labels") else ""
    jd_text = " ".join([
        job.get("positionName", ""),
        job.get("duty", "") or "",
        job.get("qualification", "") or "",
        labels_text,
    ])
    tech_score = 0
    triggered = []
    for kw, score in HIGH_TECH_SIGNALS:
        if kw.lower() in jd_text.lower():
            tech_score += score
            triggered.append(f"{kw}(+{score})")
    product_score = sum(1 for kw in PRODUCT_SIGNALS if kw.lower() in jd_text.lower())
    return tech_score, triggered, product_score


def should_exclude_by_tech(job: dict):
    tech_score, signals, product_score = calc_tech_score(job)
    detail = f"tech_score={tech_score}, 信号词={signals}, product_score={product_score}"
    if tech_score >= 5 and product_score <= 1:
        return True, f"技术门槛过高 | {detail}"
    if tech_score >= 7:
        return True, f"极高技术门槛 | {detail}"
    return False, f"技术门槛可接受 | {detail}"


def classify_job_type(job: dict) -> str:
    title = job.get("positionName", "")
    labels_text = " ".join(job.get("labels") or []) if job.get("labels") else ""
    jd = (job.get("duty", "") or "") + " " + labels_text
    text = title + " " + jd
    if any(kw in text for kw in ["Agent产品", "AI原生", "AIGC产品", "大模型应用", "多模态产品"]):
        return "AI原生"
    if any(kw in text for kw in ["模型产品", "评测产品", "训练数据", "模型能力", "基准测试"]):
        return "AI模型产品"
    if any(kw in text for kw in ["基础架构", "基础设施", "PaaS", "IaaS", "算力", "MLOps"]):
        return "AI平台架构"
    return "AI赋能"


# ── 格式化 ────────────────────────────────────────────────────

def format_job(job: dict, idx: int) -> dict:
    city = job.get("workplace", "") or ""
    bu = job.get("jobProjectName", "") or ""
    job_type = infer_job_type(job)
    position_id = job.get("positionId", "")
    url = f"https://job.xiaohongshu.com/position/{position_id}"
    labels_text = " ".join(job.get("labels") or []) if job.get("labels") else ""
    jd_text = "\n".join(filter(None, [
        job.get("duty"), job.get("qualification"), labels_text or None,
    ]))
    title = job.get("positionName", "")
    job_category = classify_job_type(job)
    cats = [job_category]
    if bu:
        cats.append(bu)
    if any(k in title for k in ["高级", "资深", "高阶", "Senior"]):
        cats.append("高级")
    elif job_type == "实习" or "实习" in title:
        cats.append("实习生")
    else:
        cats.append("中级")
    duty = job.get("duty") or ""
    summary = duty[:60].strip().replace("\n", "，") + "..." if len(duty) > 60 else duty
    keywords = list(set([kw for kw in AI_KEYWORDS if kw in jd_text or kw in title]))[:6]

    return {
        "id": idx,
        "company": "小红书",
        "bu": bu,
        "title": title,
        "city": city,
        "type": job_type,
        "cats": cats,
        "url": url,
        "jd": jd_text,
        "summary": summary,
        "keywords": keywords,
    }


def make_audit_record(job: dict, decision: str, stage: str, reason: str) -> dict:
    position_id = job.get("positionId", "")
    job_type = infer_job_type(job)
    bu = job.get("jobProjectName", "") or ""
    return {
        "decision": decision,
        "stage": stage,
        "reason": reason,
        "title": job.get("positionName", ""),
        "type": job_type,
        "bu": bu,
        "url": f"https://job.xiaohongshu.com/position/{position_id}",
        "positionId": str(position_id),
    }


# ── 采集（含重试兜底）────────────────────────────────────────

def fetch_single_page(payload: dict, page_no: int) -> tuple:
    """
    单页请求，含重试机制。
    返回 (jobs_list, total_pages) 或在失败时返回 (None, None)
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(API_URL, json=payload, headers=HEADERS, timeout=15)

            # 兜底：429 限流，等待后重试
            if resp.status_code == 429:
                wait = RETRY_DELAY * attempt
                print(f" ⏳限流，等待{wait}s后重试({attempt}/{MAX_RETRIES})", end="", flush=True)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()

            # 兜底：API success 非 true
            if not data.get("success"):
                print(f" ⚠️ API异常[{data.get('statusCode')}]: {data.get('alertMsg')}", end="", flush=True)
                return None, None

            # 兜底：data 字段为 None 或缺失
            if not data.get("data"):
                print(f" ⚠️ 响应data为空", end="", flush=True)
                return [], 0

            page_data = data["data"]
            jobs = page_data.get("list") or []
            total_pages = max(page_data.get("totalPage", 0), 0)  # 直接读 totalPage，无嵌套

            return jobs, total_pages

        except requests.exceptions.Timeout:
            print(f" ⏰超时({attempt}/{MAX_RETRIES})", end="", flush=True)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.exceptions.ConnectionError:
            print(f" 🔌连接错误({attempt}/{MAX_RETRIES})", end="", flush=True)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.exceptions.RequestException as e:
            print(f" ❌请求异常: {type(e).__name__}({attempt}/{MAX_RETRIES})", end="", flush=True)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # 兜底：响应非 JSON 或结构不符合预期
            print(f" ❌响应解析失败: {type(e).__name__}", end="", flush=True)
            return None, None

    # 所有重试耗尽
    print(f" ❌第{page_no}页放弃（已重试{MAX_RETRIES}次）", end="", flush=True)
    return None, None


def fetch_jobs_by_keyword(keyword: str, recruit_type: str) -> list:
    all_jobs = []
    page_no = 1
    total_pages = None
    label = RECRUIT_TYPE_MAP.get(recruit_type, recruit_type)
    print(f"  [{label}] 「{keyword}」", end="", flush=True)

    while True:
        # 兜底：超过最大页数保护
        if page_no > MAX_PAGES:
            print(f" ⚠️超过最大页数{MAX_PAGES}，停止", end="", flush=True)
            break

        payload = {
            "recruitType": recruit_type,
            "positionName": keyword,
            "pageNum": page_no,
            "pageSize": PAGE_SIZE,
        }

        jobs, tp = fetch_single_page(payload, page_no)

        # 请求彻底失败，跳过剩余页
        if jobs is None:
            print(f" ⚠️跳过剩余页", end="", flush=True)
            break

        # 首页打印总量
        if page_no == 1:
            total_pages = tp
            # 兜底：totalPage=0 说明没有结果，直接退出
            if total_pages == 0:
                print(f" →无结果", end="", flush=True)
                break
            print(f" →共{total_pages}页", end="", flush=True)

        # 注入 recruit_type，供后续类型推断使用
        for job in jobs:
            job["_recruit_type"] = recruit_type

        all_jobs.extend(jobs)
        print(f" [{page_no}✓]", end="", flush=True)

        # 兜底：当页返回空列表，即使还有页码也停止（防API异常导致无限空翻页）
        if not jobs:
            print(f" ⚠️空页，停止翻页", end="", flush=True)
            break

        if page_no >= total_pages:
            break

        page_no += 1
        time.sleep(random.uniform(1.0, 1.8))

    print()
    return all_jobs


def deduplicate(jobs: list) -> list:
    seen = set()
    result = []
    for job in jobs:
        uid = job.get("positionId")
        if uid and uid not in seen:
            seen.add(uid)
            result.append(job)
    return result


# ── 安全写文件 ────────────────────────────────────────────────

def safe_write_json(filepath: str, data, label: str) -> bool:
    """兜底：文件写入失败时打印错误但不崩溃，返回是否成功"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ {filepath} ({label})")
        return True
    except OSError as e:
        print(f"  ❌ 写入 {filepath} 失败: {e}")
        # 兜底：尝试写到当前目录的备份文件
        backup = f"backup_{filepath}"
        try:
            with open(backup, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  ⚠️ 已写入备份文件: {backup}")
        except OSError:
            print(f"  ❌ 备份文件也写入失败，数据丢失！请检查磁盘空间和权限")
        return False


# ── 主函数 ────────────────────────────────────────────────────

def main():
    start_time = datetime.now()
    print("=" * 60)
    print(f"小红书招聘采集  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── Step 1: 采集 ──
    print("\n[Step 1] 采集原始数据")
    all_raw = []
    for recruit_type in ["social", "campus"]:
        print(f"\n  ▶ {RECRUIT_TYPE_MAP[recruit_type]}")
        for keyword in KEYWORDS:
            try:
                jobs = fetch_jobs_by_keyword(keyword, recruit_type)
                all_raw.extend(jobs)
            except Exception as e:
                # 兜底：单个关键词采集崩溃，记录后继续下一个
                print(f"\n  ❌ 关键词「{keyword}」采集异常: {type(e).__name__}: {e}，跳过继续")

    print(f"\n  采集完成，原始: {len(all_raw)} 条")

    # 兜底：采集结果为空
    if not all_raw:
        print("\n  ⚠️ 未采集到任何数据，请检查网络或 API 是否可用，程序退出")
        return

    # ── Step 2: 去重 ──
    print("\n[Step 2] 去重")
    unique = deduplicate(all_raw)
    print(f"  去重后: {len(unique)} 条（去除 {len(all_raw) - len(unique)} 条重复）")

    # ── Step 3: 筛选 ──
    print("\n[Step 3] 筛选")
    audit_log = []
    kept = []

    for job in unique:
        try:
            # 第一关：硬排除
            excluded, reason = hard_exclude(job)
            if excluded:
                audit_log.append(make_audit_record(job, "EXCLUDED_HARD", "硬排除", reason))
                continue

            # 第二关：AI 相关性
            related, ai_reason = is_ai_related(job)
            if not related:
                audit_log.append(make_audit_record(job, "EXCLUDED_NOT_AI", "AI相关性", ai_reason))
                continue

            # 第三关：技术门槛
            too_tech, tech_reason = should_exclude_by_tech(job)
            if too_tech:
                audit_log.append(make_audit_record(job, "EXCLUDED_TECH", "技术门槛", tech_reason))
                continue

            # 通过所有关卡
            full_reason = f"AI相关({ai_reason}) | {tech_reason}"
            audit_log.append(make_audit_record(job, "KEPT", "全部通过", full_reason))
            kept.append(job)

        except Exception as e:
            # 兜底：单条岗位筛选崩溃，保守处理：保留该岗位并记录异常
            title = job.get("positionName", "未知")
            print(f"\n  ⚠️ 岗位「{title}」筛选异常: {e}，保守保留")
            audit_log.append(make_audit_record(job, "KEPT_BY_ERROR", "筛选异常", f"筛选过程报错: {e}，保守保留"))
            kept.append(job)

    cnt = lambda d: sum(1 for r in audit_log if r["decision"] == d)
    print(f"  硬排除:     {cnt('EXCLUDED_HARD'):>3} 条")
    print(f"  非AI相关:   {cnt('EXCLUDED_NOT_AI'):>3} 条")
    print(f"  技术门槛高: {cnt('EXCLUDED_TECH'):>3} 条")
    print(f"  异常保留:   {cnt('KEPT_BY_ERROR'):>3} 条")
    print(f"  ✅ 保留:     {cnt('KEPT'):>3} 条")

    # ── Step 4: 格式化 ──
    print("\n[Step 4] 格式化")
    formatted = []
    for i, job in enumerate(kept):
        try:
            formatted.append(format_job(job, i + 1))
        except Exception as e:
            # 兜底：单条格式化失败，输出最小化记录
            print(f"  ⚠️ 岗位「{job.get('positionName','?')}」格式化失败: {e}，输出原始数据")
            formatted.append({
                "id": i + 1,
                "company": "小红书",
                "title": job.get("positionName", ""),
                "url": f"https://job.xiaohongshu.com/position/{job.get('positionId', '')}",
                "_format_error": str(e),
            })

    # 实习优先，其次校招，最后社招
    type_order = {"实习": 0, "校招": 1, "社招": 2}
    formatted.sort(key=lambda x: type_order.get(x.get("type", ""), 3))

    # ── Step 5: 写文件 ──
    print("\n[Step 5] 写入文件")

    safe_write_json("xiaohongshu_jobs.json", formatted, f"{len(formatted)} 条最终岗位")

    audit_output = {
        "_meta": {
            "generated_at": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": round((datetime.now() - start_time).total_seconds(), 1),
            "total_raw": len(all_raw),
            "after_dedup": len(unique),
            "kept": cnt("KEPT"),
            "kept_by_error": cnt("KEPT_BY_ERROR"),
            "excluded_hard": cnt("EXCLUDED_HARD"),
            "excluded_not_ai": cnt("EXCLUDED_NOT_AI"),
            "excluded_tech": cnt("EXCLUDED_TECH"),
        },
        "KEPT": [r for r in audit_log if r["decision"] == "KEPT"],
        "EXCLUDED_TECH": [r for r in audit_log if r["decision"] == "EXCLUDED_TECH"],
        "EXCLUDED_NOT_AI": [r for r in audit_log if r["decision"] == "EXCLUDED_NOT_AI"],
        "EXCLUDED_HARD": [r for r in audit_log if r["decision"] == "EXCLUDED_HARD"],
        "KEPT_BY_ERROR": [r for r in audit_log if r["decision"] == "KEPT_BY_ERROR"],
    }
    safe_write_json("xiaohongshu_audit.json", audit_output, f"{len(audit_log)} 条决策记录")

    print(f"\n{'='*60}")
    print(f"完成  耗时 {round((datetime.now()-start_time).total_seconds(),1)}s")
    print(f"  实习 {sum(1 for j in formatted if j.get('type')=='实习')} 条")
    print(f"  校招 {sum(1 for j in formatted if j.get('type')=='校招')} 条")
    print(f"  社招 {sum(1 for j in formatted if j.get('type')=='社招')} 条")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断，程序退出")
    except Exception as e:
        print(f"\n\n❌ 未预期的严重错误: {type(e).__name__}: {e}")
        print("请检查网络连接和 API 是否可用")
        raise
