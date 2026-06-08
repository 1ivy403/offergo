"""统一 jobs 输出目录：CI 用 OFFERGO_JOBS_DIR，本地 fallback 到 config 或 Desktop。"""
import os
from pathlib import Path


def get_jobs_dir() -> str:
    env = os.environ.get("OFFERGO_JOBS_DIR")
    if env:
        p = Path(env)
    else:
        try:
            from config import JOBS_DIR

            p = JOBS_DIR
        except ImportError:
            p = Path.home() / "Desktop/岗位搜集/jobs"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)
