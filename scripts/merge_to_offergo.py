#!/usr/bin/env python3
"""合并 jobs/*_jobs.json → jobs.json，并生成 meta.json / health.json。"""
import json
import os
import re
from collections import Counter
from datetime import datetime
from glob import glob
from pathlib import Path
from zoneinfo import ZoneInfo

from config import (
    JOBS_DIR,
    OFFERGO_HEALTH_JSON,
    OFFERGO_JOBS_JSON,
    OFFERGO_META_JSON,
    PHASE2_REPORT,
)

SRC = JOBS_DIR
OUT = OFFERGO_JOBS_JSON

COMPANY_FROM_FILE = {
    "tencent": "腾讯",
    "bytedance": "字节跳动",
    "meituan": "美团",
    "jd": "京东",
    "didi": "滴滴",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "antgroup": "蚂蚁集团",
    "bilibili": "哔哩哔哩",
    "ctrip": "携程",
    "iflytek": "科大讯飞",
    "nio": "蔚来",
    "lixiang": "理想汽车",
    "pdd": "拼多多",
    "xiaomi": "小米",
    "netease": "网易",
    "alibaba": "阿里巴巴",
    "taotian": "淘天集团",
    "eleme": "淘宝闪购",
    "wangyi": "网易",
}


def now_beijing() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


def load_json(path: Path, default):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return default


def infer_company(filename: str, item: dict) -> str:
    if item.get("company"):
        return item["company"]
    base = filename.replace("_jobs.json", "")
    for prefix, name in COMPANY_FROM_FILE.items():
        if base.startswith(prefix):
            return name
    return base.split("_")[0]


def infer_type(filename: str, item: dict) -> str:
    if item.get("type"):
        t = item["type"]
        if t in ("全职", "正式"):
            return "社招"
        if "实习" in t:
            return "实习"
        return t
    fn = filename.lower()
    if "_intern_" in fn or fn.endswith("_intern_jobs.json"):
        return "实习"
    if "_campus_" in fn or "campus" in fn:
        return "校招"
    if "_social_" in fn or "social" in fn:
        return "社招"
    return "社招"


def norm_city(item: dict) -> str:
    for key in ("city", "location", "area"):
        v = item.get(key)
        if v:
            s = str(v).replace("市", "").replace(",", "、").replace("，", "、")
            return re.sub(r"\s+", "", s)
    return "未注明"


def norm_bu(item: dict) -> str:
    bu = item.get("bu") or item.get("dept") or ""
    if bu:
        return str(bu)
    bg = item.get("bg") or ""
    prod = item.get("product") or ""
    if bg or prod:
        return " - ".join(x for x in [bg, prod] if x)
    cat = item.get("cat") or item.get("category") or ""
    return str(cat) if cat else ""


def norm_tags(item: dict) -> list:
    cats = item.get("cats")
    if isinstance(cats, list):
        return [str(x) for x in cats if x]
    if isinstance(cats, dict):
        return [str(v) for v in cats.values() if v]
    if item.get("cat"):
        return [str(item["cat"])]
    if item.get("category"):
        return [str(item["category"])]
    return []


def normalize_item(item: dict, filename: str) -> dict:
    title = item.get("title") or ""
    jd = item.get("jd") or item.get("summary") or ""
    company = infer_company(filename, item)
    typ = infer_type(filename, item)
    city = norm_city(item)
    bu = norm_bu(item)
    url = item.get("url") or ""
    tags = norm_tags(item)
    out = {
        "company": company,
        "bu": bu,
        "title": title,
        "city": city,
        "type": typ,
        "tags": tags,
        "jd": jd,
        "url": url,
    }
    if item.get("summary"):
        out["summary"] = item["summary"]
    if item.get("keywords"):
        out["keywords"] = item["keywords"]
    if item.get("url_expired"):
        out["url_expired"] = True
    return out


def count_expired_in_jobs() -> int:
    total = 0
    for fp in sorted(SRC.glob("*_jobs.json")):
        with fp.open(encoding="utf-8") as f:
            jobs = json.load(f)
        if isinstance(jobs, list):
            total += sum(1 for j in jobs if j.get("url_expired"))
    return total


def scraper_status(count: int, prev_count: int) -> str:
    if count == 0:
        return "warn_zero"
    if prev_count > 0 and count < prev_count * 0.5:
        return "warn_drop"
    return "ok"


def build_health() -> dict:
    prev_health = load_json(OFFERGO_HEALTH_JSON, {})
    prev_map = {s["name"]: s.get("count", 0) for s in prev_health.get("scrapers", [])}

    scrapers = []
    warnings = []
    for fp in sorted(SRC.glob("*_jobs.json")):
        name = fp.name.replace("_jobs.json", "")
        with fp.open(encoding="utf-8") as f:
            jobs = json.load(f)
        count = len(jobs) if isinstance(jobs, list) else 0
        prev_count = prev_map.get(name, count)
        status = scraper_status(count, prev_count)

        if status == "warn_zero":
            warnings.append(f"{name}: 本次 0 条，上次 {prev_count} 条")
        elif status == "warn_drop":
            warnings.append(f"{name}: 本次 {count} 条，上次 {prev_count} 条（跌超 50%）")

        scrapers.append({
            "name": name,
            "count": count,
            "prev_count": prev_count,
            "status": status,
        })

    return {
        "refreshed_at": now_beijing(),
        "scrapers": scrapers,
        "warnings": warnings,
        "actions_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "status": "ok",
    }


def write_meta(total: int, expired: int):
    prev_meta = load_json(OFFERGO_META_JSON, {})
    prev_total = prev_meta.get("total", 0)
    meta = {
        "refreshed_at": now_beijing(),
        "total": total,
        "added": max(0, total - prev_total),
        "expired": expired,
        "status": "ok",
    }
    OFFERGO_META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"meta -> {OFFERGO_META_JSON} total={total} added={meta['added']} expired={expired}")


def main():
    files = sorted(glob(str(SRC / "*_jobs.json")))
    merged = []
    seen = set()
    for fp in files:
        if os.path.basename(fp).startswith("_"):
            continue
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            continue
        fname = os.path.basename(fp)
        for item in data:
            if not item or not item.get("title"):
                continue
            norm = normalize_item(item, fname)
            key = (norm["company"], norm["title"], norm["url"] or norm.get("city", ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(norm)

    type_order = {"实习": 0, "校招": 1, "社招": 2}
    merged.sort(key=lambda x: (x["company"], type_order.get(x["type"], 9), x["title"]))
    for i, j in enumerate(merged, 1):
        j["id"] = i

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    expired = count_expired_in_jobs()
    write_meta(len(merged), expired)

    health = build_health()
    OFFERGO_HEALTH_JSON.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"health -> {OFFERGO_HEALTH_JSON} warnings={len(health['warnings'])}")

    c = Counter(j["company"] for j in merged)
    print(f"merged {len(merged)} jobs -> {OUT}")
    print("top companies:", c.most_common(10))


if __name__ == "__main__":
    main()
