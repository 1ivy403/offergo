#!/usr/bin/env python3
"""Phase 1: run scrapers (GitHub Actions / 本地). 排除需 Cookie 的 alibaba / taotian。"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from config import JOBS_DIR, SCRIPTS_DIR

LOG = SCRIPTS_DIR / "phase1_run.log"
REPORT = SCRIPTS_DIR / "phase1_report.json"

EXCLUDED = {"alibaba_scraper.py", "taotian_scraper.py"}

# 额外脚本（cwd 指向 jobs 目录，输出 *_jobs.json 到该目录）
JOBS_CWD_SCRIPTS = {
    "bytedance_scraper.py",
    "小红书_scraper.py",
}


def discover_scripts():
    found = []
    for p in sorted(SCRIPTS_DIR.glob("*_scrape.py")):
        found.append(p.name)
    for name in ["wangyi_scraper.py", "bytedance_scraper.py", "小红书_scraper.py"]:
        if (SCRIPTS_DIR / name).exists() and name not in found:
            found.append(name)
    return [n for n in found if n not in EXCLUDED]


def script_entry(name: str):
    path = str(SCRIPTS_DIR / name)
    cwd = str(JOBS_DIR if name in JOBS_CWD_SCRIPTS else SCRIPTS_DIR)
    return path, cwd


MAX_SECONDS = 900


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n"
    print(line, end="", flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def main():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    results = []
    env = os.environ.copy()
    env["OFFERGO_JOBS_DIR"] = str(JOBS_DIR)

    for name in discover_scripts():
        script, cwd = script_entry(name)
        log(f"START {name} cwd={cwd}")
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, script],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=MAX_SECONDS,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            ok = proc.returncode == 0
            err = "" if ok else f"exit_code={proc.returncode}"
        except subprocess.TimeoutExpired as e:
            ok = False
            out = (e.stdout or "") + (e.stderr or "")
            err = f"timeout({MAX_SECONDS}s)"
        except Exception as e:
            ok = False
            out = ""
            err = str(e)

        dur = round(time.time() - t0, 1)
        tail = "\n".join(out.strip().splitlines()[-20:]) if out else ""
        status = "ok" if ok else "fail"
        log(f"END {name} status={status} dur={dur}s")
        if not ok:
            log(f"TAIL {name}:\n{tail}")

        with LOG.open("a", encoding="utf-8") as f:
            if out:
                f.write(f"\n--- output {name} ---\n{out}\n")

        results.append({
            "script": name,
            "path": script,
            "status": status,
            "duration_s": dur,
            "error": err,
            "tail": tail[-800:] if tail else "",
        })

    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_n = sum(1 for r in results if r["status"] == "ok")
    log(f"DONE ok={ok_n} fail={len(results)-ok_n} report={REPORT}")


if __name__ == "__main__":
    main()
