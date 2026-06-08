#!/usr/bin/env python3
"""GitHub Actions 失败时写入 meta.json / health.json 状态。"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from config import OFFERGO_HEALTH_JSON, OFFERGO_META_JSON


def now_beijing() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


def load_json(path, default):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return default


def main():
    ts = now_beijing()
    meta = load_json(OFFERGO_META_JSON, {})
    meta.update({"refreshed_at": ts, "status": "failed"})
    OFFERGO_META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    health = load_json(OFFERGO_HEALTH_JSON, {})
    health.update({
        "refreshed_at": ts,
        "status": "failed",
        "failure_reason": os.environ.get("GITHUB_JOB_STATUS", "failure"),
        "actions_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "warnings": health.get("warnings", []) + ["GitHub Actions 刷新失败，请查看 Actions 日志"],
    })
    OFFERGO_HEALTH_JSON.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"marked failure at {ts}")


if __name__ == "__main__":
    main()
