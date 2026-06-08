"""OfferGo 仓库路径配置（本地 workbench / GitHub Actions 通用）。"""
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
JOBS_DIR = REPO_ROOT / "jobs"
OFFERGO_JOBS_JSON = REPO_ROOT / "jobs.json"
OFFERGO_META_JSON = REPO_ROOT / "meta.json"
OFFERGO_HEALTH_JSON = REPO_ROOT / "health.json"
PHASE2_REPORT = SCRIPTS_DIR / "phase2_url_report.json"
ROOT = SCRIPTS_DIR
