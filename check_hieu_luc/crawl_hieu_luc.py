#!/usr/bin/env python3
import asyncio
import random
from datetime import datetime, timedelta
from collections import Counter
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ======================
# CONFIG
# ======================
ROOT_URLS = [
    "https://thuvienphapluat.vn/van-ban/Bao-hiem/Luat-bao-hiem-y-te-2008-25-2008-QH12-82196.aspx",
    "https://thuvienphapluat.vn/van-ban/The-thao-Y-te/Luat-15-2023-QH15-kham-benh-chua-benh-372143.aspx",
]

COOKIES_FILE = "check_hieu_luc/cookies.txt"
OUTPUT_TXT = "check_hieu_luc/output_related_status.txt"
MAX_RELATED_PER_ROOT = 5
DELAY_RANGE = (2, 4)
RETRY_DELAY_RANGE = (5, 8)

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

def load_cookies_netscape(path):
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
    info(f"Loaded {len(cookies)} cookies")
    return cookies

# ======================
# STEP 1: GET RELATED URLS
# ======================
async def get_related_urls(page, root_url):
    root_url = root_url.strip()
    info(f"ROOT → {root_url}")

    await page.goto(root_url, timeout=200000)
    await page.wait_for_load_state("domcontentloaded")

    try:
        await page.wait_for_selector("#aLuocDo", timeout=30000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=30000)
    except PlaywrightTimeoutError:
        warn("Không load được lược đồ (ROOT)")
        return []

    links = await page.query_selector_all(
        "#cmDiagram a[id$='_titleHyperLink']"
    )

    results = []
    seen = set()

    for a in links:
        if len(results) >= MAX_RELATED_PER_ROOT:
            break

        href = (await a.get_attribute("href") or "").strip()
        title = normalize(await a.inner_text())

        if not href or href in seen or href == root_url:
            continue

        seen.add(href)
        results.append((title, href))

    success(f"Found {len(results)} related documents (TEST MODE)")
    return results

# ======================
# STEP 2: CHECK STATUS
# ======================
async def check_document_status(page, title, url):
    info(f"Check → {title}")

    await page.goto(url, timeout=200000)
    await page.wait_for_load_state("domcontentloaded")

    try:
        await page.wait_for_selector("#aLuocDo", timeout=20000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=20000)
    except PlaywrightTimeoutError:
        warn("Không có tab lược đồ")
        return {"status": "UNKNOWN", "title": title, "url": url}

    container = await page.query_selector("#viewingDocument")
    if not container:
        warn("Không tìm thấy metadata")
        return {"status": "UNKNOWN", "title": title, "url": url}

    raw = {}
    for att in await container.query_selector_all(".att"):
        k = await att.query_selector(".hd")
        v = await att.query_selector(".ds")
        if k and v:
            raw[
                normalize(await k.inner_text()).rstrip(":")
            ] = normalize(await v.inner_text())

    tinh_trang = raw.get("Tình trạng", "")
    ngay_hieu_luc = raw.get("Ngày hiệu lực", "")
    so_hieu = raw.get("Số hiệu", "")

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today - timedelta(days=7)
    hieu_luc_date = parse_date(ngay_hieu_luc)

    status = "UNKNOWN"

    # ===== LOGIC CHUẨN HÓA =====
    if "Hết hiệu lực" in tinh_trang:
        status = "EXPIRED"
    elif hieu_luc_date:
        if hieu_luc_date < today:
            status = "EXPIRED"
        elif hieu_luc_date >= seven_days_ago:
            status = "NEW"
        else:
            status = "VALID"

    success(f"{status} | {so_hieu} | {ngay_hieu_luc}")

    return {
        "status": status,
        "title": title,
        "so_hieu": so_hieu,
        "ngay_hieu_luc": ngay_hieu_luc,
        "tinh_trang": tinh_trang,
        "url": url,
    }

# ======================
# MAIN
# ======================
async def main():
    info("Crawler started")
    cookies = load_cookies_netscape(COOKIES_FILE)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        for root in ROOT_URLS:
            related = await get_related_urls(page, root)

            for title, url in related:
                data = await check_document_status(page, title, url)
                results.append(data)

                delay = random.uniform(*DELAY_RANGE)
                info(f"Sleep {delay:.1f}s")
                await page.wait_for_timeout(int(delay * 1000))

        await browser.close()

    # ======================
    # OUTPUT
    # ======================
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        for r in results:
            f.write(
                f"[{r['status']}] {r['title']} | {r.get('so_hieu','')} | "
                f"{r.get('ngay_hieu_luc','')} | {r['url']}\n"
            )

    stats = Counter(r["status"] for r in results)
    info("CRAWL SUMMARY")
    for k, v in stats.items():
        info(f"{k}: {v}")

    success(f"DONE – Output saved to {OUTPUT_TXT}")

if __name__ == "__main__":
    asyncio.run(main())
