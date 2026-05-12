#!/usr/bin/env python3
import asyncio
import json
import re
import sys
from datetime import datetime
from collections import Counter
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ======================
# CONFIG
# ======================
BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "status_cache.json"
COOKIES_FILE = BASE_DIR / "cookies.txt"
DATA_FILE = BASE_DIR / "data.json"

RECHECK_STATUSES = {"LOAD_ERROR", "NO_LUOCDO", "PARSE_ERROR"}

# ======================
# LOGGING
# ======================
def log(level, msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {msg}")

def info(msg): log("INFO", msg)
def warn(msg): log("WARN", msg)
def error(msg): log("ERROR", msg)
def success(msg): log("SUCCESS", msg)

# ======================
# UTILS
# ======================
def normalize(text: str) -> str:
    return " ".join(text.split()) if text else ""

def parse_date(text: str):
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y")
    except:
        return None

def extract_expired_date(text: str):
    match = re.search(r'(\d{2}/\d{2}/\d{4})', text)
    if match:
        return match.group(1)
    return None

def load_json_safe(path):
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# ======================
# PHÂN LOẠI TÌNH TRẠNG
# ======================
def classify_status(raw_status: str):
    if not raw_status:
        return "PARSE_ERROR"

    text = raw_status.lower()

    if "còn hiệu lực" in text:
        return "VALID"

    if "hết hiệu lực" in text:
        return "EXPIRED"

    if "không còn phù hợp" in text:
        return "NOT_APPLICABLE"

    return "OTHER"

# ======================
# CHECK DOCUMENT
# ======================
async def check_document(page, title, url):

    info(f"Checking → {title}")

    try:
        await page.goto(url, timeout=200000)
        await page.wait_for_load_state("domcontentloaded")
    except:
        warn("Không load được trang")
        return {
            "normalized_status": "LOAD_ERROR",
            "raw_status": None
        }

    try:
        await page.wait_for_selector("#aLuocDo", timeout=20000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=20000)
    except PlaywrightTimeoutError:
        warn("Không load được tab Lược đồ")
        return {
            "normalized_status": "NO_LUOCDO",
            "raw_status": None
        }

    container = await page.query_selector("#viewingDocument")
    if not container:
        warn("Không tìm thấy metadata")
        return {
            "normalized_status": "PARSE_ERROR",
            "raw_status": None
        }

    raw_data = {}
    for att in await container.query_selector_all(".att"):
        k = await att.query_selector(".hd")
        v = await att.query_selector(".ds")
        if k and v:
            raw_data[
                normalize(await k.inner_text()).rstrip(":")
            ] = normalize(await v.inner_text())

    raw_status = raw_data.get("Tình trạng", "")
    so_hieu = raw_data.get("Số hiệu", "")

    normalized = classify_status(raw_status)

    expired_date = None
    if normalized == "EXPIRED":
        expired_date = extract_expired_date(raw_status)

    info(f"Raw status: {raw_status}")
    info(f"Normalized: {normalized}")

    return {
        "normalized_status": normalized,
        "raw_status": raw_status,
        "so_hieu": so_hieu,
        "expired_date": expired_date,
        "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

# ======================
# MAIN
# ======================
async def main():

    if len(sys.argv) != 2:
        print("Usage: python check_hieu_luc.py dd/mm/YYYY")
        sys.exit(1)

    input_date = parse_date(sys.argv[1])
    if not input_date:
        print("Sai định dạng ngày")
        sys.exit(1)

    documents = load_json_safe(DATA_FILE)
    cache = load_json_safe(CACHE_FILE)

    info(f"Tổng văn bản: {len(documents)}")
    info(f"Đã có cache: {len(cache)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        cookies = load_json_safe(COOKIES_FILE) if COOKIES_FILE.suffix == ".json" else []
        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()

        for title, url in documents.items():

            # ======================
            # RECHECK LOGIC
            # ======================
            if title in cache:
                old_status = cache[title].get("normalized_status")

                if old_status not in RECHECK_STATUSES:
                    info(f"Skip (đã có trạng thái hợp lệ) → {title}")
                    continue
                else:
                    info(f"Rechecking do lỗi trước đó → {title}")

            result = await check_document(page, title, url)

            cache[title] = result
            save_cache(cache)

        await browser.close()

    # ======================
    # PHÂN LOẠI THEO INPUT DATE
    # ======================
    expired_before = []

    for title, data in cache.items():
        if data.get("normalized_status") == "EXPIRED":
            if data.get("expired_date"):
                d = parse_date(data["expired_date"])
                if d and d < input_date:
                    expired_before.append((title, data))

    date_str = input_date.strftime("%d_%m_%Y")
    expired_file = BASE_DIR / f"expired_before_{date_str}.txt"

    with open(expired_file, "w", encoding="utf-8") as f:
        for title, data in expired_before:
            f.write(
                f"{title} | {data.get('so_hieu','')} | "
                f"{data.get('raw_status','')} \n"
            )

    stats = Counter(d.get("normalized_status") for d in cache.values())

    info("========== SUMMARY ==========")
    for k, v in stats.items():
        info(f"{k}: {v}")

    success(f"Expired list saved → {expired_file}")
    success(f"Cache saved → {CACHE_FILE}")

if __name__ == "__main__":
    asyncio.run(main())