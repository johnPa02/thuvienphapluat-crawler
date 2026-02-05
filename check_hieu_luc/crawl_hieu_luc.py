#!/usr/bin/env python3
import asyncio
import random
import re
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ======================
# CONFIG
# ======================
DEFAULT_ROOT_URLS = [
    "https://thuvienphapluat.vn/van-ban/Bao-hiem/Luat-bao-hiem-y-te-2008-25-2008-QH12-82196.aspx",
    "https://thuvienphapluat.vn/van-ban/The-thao-Y-te/Luat-15-2023-QH15-kham-benh-chua-benh-372143.aspx",
]

DEFAULT_COOKIES_FILE = "check_hieu_luc/cookies.txt"
DELAY_RANGE = (2, 4)

# ======================
# FASTAPI
# ======================
app = FastAPI(
    title="API kiểm tra hiệu lực văn bản pháp luật",
    version="2.1.0"
)

class CrawlRequest(BaseModel):
    cookies_file: Optional[str] = Field(
        default=None,
        description="Đường dẫn file cookies Netscape"
    )
    root_urls: Optional[List[str]] = None
    n: Optional[int] = Field(
        default=None,
        description="Số văn bản cần crawl mỗi root (None = toàn bộ)"
    )
    crawl_date: Optional[str] = Field(
        default=None,
        description="Ngày crawl hiện tại (YYYY-MM-DD)"
    )
    prev_crawl_date: Optional[str] = Field(
        default=None,
        description="Ngày crawl trước đó (YYYY-MM-DD)"
    )

# ======================
# UTILS
# ======================
def normalize(text: str) -> str:
    return " ".join(text.split()) if text else ""

def parse_date_ddmmyyyy(text: str):
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y")
    except:
        return None

def parse_date_yyyy_mm_dd(text: str):
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except:
        raise HTTPException(
            status_code=400,
            detail=f"Sai định dạng ngày YYYY-MM-DD: {text}"
        )

def extract_expired_date(tinh_trang: str):
    """
    'Hết hiệu lực: 01/10/2025' -> datetime
    """
    m = re.search(r"Hết hiệu lực:\s*(\d{2}/\d{2}/\d{4})", tinh_trang)
    if not m:
        return None
    return parse_date_ddmmyyyy(m.group(1))

def load_cookies_netscape(path: str):
    import os

    if not os.path.isfile(path):
        raise HTTPException(
            status_code=400,
            detail=f"cookies_file không tồn tại: {path}"
        )

    cookies = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 7:
                continue
            cookies.append({
                "domain": parts[0].lstrip("."),
                "path": parts[2],
                "secure": parts[4] == "TRUE",
                "name": parts[5],
                "value": parts[6],
            })
    return cookies

# ======================
# STEP 1: GET RELATED
# ======================
async def get_related_urls(page, root_url: str, limit: Optional[int]):
    await page.goto(root_url, timeout=200000)
    await page.wait_for_load_state("domcontentloaded")

    try:
        await page.wait_for_selector("#aLuocDo", timeout=30000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=30000)
    except PlaywrightTimeoutError:
        return []

    links = await page.query_selector_all("#cmDiagram a[id$='_titleHyperLink']")

    results = []
    seen = set()

    for a in links:
        if limit is not None and len(results) >= limit:
            break

        href = (await a.get_attribute("href") or "").strip()
        title = normalize(await a.inner_text())

        if not href or href in seen or href == root_url:
            continue

        seen.add(href)
        results.append((title, href))

    return results

# ======================
# STEP 2: FETCH METADATA
# ======================
async def fetch_document_metadata(page, title: str, url: str):
    await page.goto(url, timeout=200000)
    await page.wait_for_load_state("domcontentloaded")

    try:
        await page.wait_for_selector("#aLuocDo", timeout=20000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=20000)
    except PlaywrightTimeoutError:
        return None

    container = await page.query_selector("#viewingDocument")
    if not container:
        return None

    raw = {}
    for att in await container.query_selector_all(".att"):
        k = await att.query_selector(".hd")
        v = await att.query_selector(".ds")
        if k and v:
            raw[
                normalize(await k.inner_text()).rstrip(":")
            ] = normalize(await v.inner_text())

    return {
        "title": title,
        "url": url,
        "so_hieu": raw.get("Số hiệu", ""),
        "ngay_hieu_luc": raw.get("Ngày hiệu lực", ""),
        "tinh_trang": raw.get("Tình trạng", ""),
    }

# ======================
# CRAWLER CORE
# ======================
async def crawl(root_urls, cookies, limit):
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        for root in root_urls:
            related = await get_related_urls(page, root, limit)

            for title, url in related:
                meta = await fetch_document_metadata(page, title, url)
                if meta:
                    results.append(meta)

                await page.wait_for_timeout(
                    int(random.uniform(*DELAY_RANGE) * 1000)
                )

        await browser.close()

    return results

# ======================
# API ENDPOINT
# ======================
@app.post("/crawl/hieu-luc")
async def crawl_hieu_luc(req: CrawlRequest):
    # ===== DEFAULT INPUT =====
    cookies_file = req.cookies_file or DEFAULT_COOKIES_FILE
    root_urls = req.root_urls or DEFAULT_ROOT_URLS

    crawl_date = (
        parse_date_yyyy_mm_dd(req.crawl_date)
        if req.crawl_date
        else datetime.now()
    )

    prev_crawl_date = (
        parse_date_yyyy_mm_dd(req.prev_crawl_date)
        if req.prev_crawl_date
        else crawl_date - timedelta(days=7)
    )

    cookies = load_cookies_netscape(cookies_file)

    data = await crawl(root_urls, cookies, req.n)

    expired_docs = []
    new_docs = []

    for d in data:
        # ===== NEW =====
        hieu_luc = parse_date_ddmmyyyy(d["ngay_hieu_luc"])
        if hieu_luc and hieu_luc > crawl_date:
            d["status"] = "NEW"
            new_docs.append(d)
            continue

        # ===== EXPIRED =====
        expired_date = extract_expired_date(d["tinh_trang"])
        if expired_date and prev_crawl_date < expired_date <= crawl_date:
            d["status"] = "EXPIRED"
            expired_docs.append(d)

    return {
        "summary": {
            "total_checked": len(data),
            "expired": len(expired_docs),
            "new": len(new_docs),
        },
        "expired_documents": expired_docs,
        "new_documents": new_docs,
    }
