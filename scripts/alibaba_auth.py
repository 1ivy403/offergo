"""阿里系招聘站 Playwright 会话：获取 CSRF + Cookie。"""
import re
from typing import Dict, Optional, Tuple

CAMPUS_BASE = "https://campus-talent.alibaba.com"
SOCIAL_BASE = "https://talent-holding.alibaba.com"


def get_alibaba_session(base_url: str, entry_path: str = "/") -> Tuple[Optional[str], Dict[str, str]]:
    """打开页面，从首个 search 请求 URL 提取 _csrf，并返回 cookies dict。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("需要 playwright: pip install playwright && playwright install chromium") from e

    csrf = {"v": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_req(req):
            m = re.search(r"_csrf=([a-f0-9\-]+)", req.url)
            if m:
                csrf["v"] = m.group(1)

        page.on("request", on_req)
        page.goto(base_url + entry_path, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(4000)
        cookies = {c["name"]: c["value"] for c in page.context.cookies()}
        browser.close()

    return csrf["v"], cookies


def get_campus_session() -> Tuple[Optional[str], Dict[str, str]]:
    return get_alibaba_session(
        CAMPUS_BASE,
        "/campus/position-list?batchId=100000540002",
    )


def get_social_session() -> Tuple[Optional[str], Dict[str, str]]:
    return get_alibaba_session(SOCIAL_BASE, "/")
