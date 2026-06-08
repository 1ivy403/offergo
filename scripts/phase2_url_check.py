#!/usr/bin/env python3
"""Phase 2: 批量 HEAD 检查 *_jobs.json 的 url，标记 url_expired。"""
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from glob import glob
from pathlib import Path

from config import JOBS_DIR, ROOT

REPORT = ROOT / "phase2_url_report.json"
WORKERS = 8
TIMEOUT = 15

LOGIN_HINTS = ("login", "passport", "signin", "auth", "sso", "moka", "feishu.cn/login")
DEAD_CODES = {404, 410, 451}

# 飞书招聘系域名不支持 HEAD，跳过校验（勿标 url_expired）
FEISHU_JOB_SKIP_HOSTS = (
    "job.feishu.cn",
    "talent.feishu.cn",
    ".jobs.feishu.cn",  # 如 nio.jobs.feishu.cn、*.jobs.feishu.cn
)


def should_skip_url_check(url: str) -> bool:
    low = (url or "").lower()
    return any(h in low for h in FEISHU_JOB_SKIP_HOSTS)


def check_url(url: str) -> dict:
    if not url or not url.startswith("http"):
        return {"status": "empty", "expired": True, "code": 0}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124 Safari/537.36"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            code = resp.getcode()
            final = resp.geturl() or url
            low = final.lower()
            if code in DEAD_CODES:
                return {"status": "dead", "expired": True, "code": code, "final": final}
            if any(h in low for h in LOGIN_HINTS):
                return {"status": "login_redirect", "expired": False, "code": code, "final": final}
            return {"status": "ok", "expired": False, "code": code, "final": final}
    except urllib.error.HTTPError as e:
        if e.code in DEAD_CODES:
            return {"status": "dead", "expired": True, "code": e.code, "final": url}
        if e.code in (403, 405):
            return {"status": "blocked", "expired": False, "code": e.code, "final": url}
        return {"status": "http_error", "expired": False, "code": e.code, "final": url}
    except Exception as e:
        return {"status": "error", "expired": False, "code": 0, "error": str(e)[:120]}


def process_file(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        jobs = json.load(f)
    if not isinstance(jobs, list):
        return {"file": path.name, "skipped": True}
    urls = {}
    skipped = 0
    for job in jobs:
        u = (job.get("url") or "").strip()
        if should_skip_url_check(u):
            job.pop("url_expired", None)
            skipped += 1
            continue
        if u:
            urls.setdefault(u, []).append(job)

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(check_url, u): u for u in urls}
        for fut in as_completed(futs):
            u = futs[fut]
            results[u] = fut.result()
            time.sleep(0.05)

    expired_count = 0
    for u, job_list in urls.items():
        r = results.get(u) or {}
        expired = bool(r.get("expired"))
        for job in job_list:
            if expired:
                job["url_expired"] = True
                expired_count += 1
            else:
                job.pop("url_expired", None)

    with path.open("w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

    return {
        "file": path.name,
        "jobs": len(jobs),
        "urls_checked": len(urls),
        "urls_skipped_feishu": skipped,
        "expired_marked": expired_count,
        "sample_dead": [u for u, r in results.items() if r.get("expired")][:5],
    }


def main():
    files = sorted(JOBS_DIR.glob("*_jobs.json"))
    print(f"Phase 2 URL check {datetime.now():%Y-%m-%d %H:%M:%S} files={len(files)}", flush=True)
    report = []
    for fp in files:
        print(f"  {fp.name} ...", flush=True)
        report.append(process_file(fp))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    total_exp = sum(r.get("expired_marked", 0) for r in report)
    print(f"DONE expired_marked={total_exp} report={REPORT}", flush=True)


if __name__ == "__main__":
    main()
