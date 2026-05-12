#!/usr/bin/env python3
import asyncio
import random
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ======================
# CONFIG
# ======================
ROOT_URLS = [
    "https://thuvienphapluat.vn/van-ban/Bao-hiem/Luat-bao-hiem-y-te-2008-25-2008-QH12-82196.aspx",
    "https://thuvienphapluat.vn/van-ban/The-thao-Y-te/Luat-15-2023-QH15-kham-benh-chua-benh-372143.aspx",
]

COOKIES_FILE = "cookies.txt"
OUTPUT_TXT = "output_related_status.txt"

DELAY_RANGE = (2, 4)
RETRY_DELAY_RANGE = (5, 8)

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
    return cookies

# ======================
# STEP 1: GET RELATED URLS
# ======================
async def get_related_urls(page, root_url):
    print(f"\n🌐 ROOT: {root_url}")
    await page.goto(root_url, timeout=200000)
    await page.wait_for_load_state("domcontentloaded")

    try:
        await page.wait_for_selector("#aLuocDo", timeout=20000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=20000)
    except PlaywrightTimeoutError:
        print("⚠️ Không load được lược đồ (ROOT) – bỏ qua")
        return []

    links = await page.query_selector_all("#cmDiagram a[id$='_titleHyperLink']")

    seen = set()
    results = []

    for a in links:
        href = await a.get_attribute("href")
        title = normalize(await a.inner_text())

        if not href or href == root_url or href in seen:
            continue

        seen.add(href)
        results.append((title, href))

    print(f"🔗 Found {len(results)} related URLs")
    return results

# ======================
# STEP 2: CHECK STATUS
# ======================
async def check_document_status(page, title, url):
    print(f"\n➡️ Check: {title}")

    try:
        await page.goto(url, timeout=200000)
        await page.wait_for_load_state("domcontentloaded")

        await page.wait_for_selector("#aLuocDo", timeout=20000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=20000)
    except PlaywrightTimeoutError:
        print("⏳ Timeout cmDiagram → RETRY")
        return {
            "status": "RETRY",
            "title": title,
            "so_hieu": "",
            "ngay_hieu_luc": "",
            "tinh_trang": "",
            "url": url,
        }

    container = await page.query_selector("#viewingDocument")
    if not container:
        print("⚠️ Không có metadata hiệu lực")
        return {
            "status": "UNKNOWN",
            "title": title,
            "so_hieu": "",
            "ngay_hieu_luc": "",
            "tinh_trang": "",
            "url": url,
        }

    raw = {}
    for att in await container.query_selector_all(".att"):
        k = await att.query_selector(".hd")
        v = await att.query_selector(".ds")
        if not k or not v:
            continue
        key = normalize((await k.inner_text()).rstrip(":"))
        val = normalize(await v.inner_text())
        raw[key] = val

    tinh_trang = raw.get("Tình trạng", "")
    ngay_hieu_luc = raw.get("Ngày hiệu lực", "")
    so_hieu = raw.get("Số hiệu", "")

    today = datetime.now()
    status = "VALID"

    if "Hết hiệu lực" in tinh_trang:
        status = "EXPIRED"
    elif "Chưa có hiệu lực" in tinh_trang:
        status = "NEW"
    else:
        d = parse_date(ngay_hieu_luc)
        if d and d > today:
            status = "NEW"

    print(f"📌 Status: {status}")
    print(f"   Số hiệu: {so_hieu}")
    print(f"   Ngày hiệu lực: {ngay_hieu_luc}")
    print(f"   Tình trạng: {tinh_trang}")

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
    cookies = load_cookies_netscape(COOKIES_FILE)

    results = []
    retry_queue = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        # -------- PHASE 1 --------
        for root in ROOT_URLS:
            related = await get_related_urls(page, root)

            for title, url in related:
                data = await check_document_status(page, title, url)

                if data["status"] == "RETRY":
                    retry_queue.append((title, url))
                else:
                    results.append(data)

                delay = random.uniform(*DELAY_RANGE)
                print(f"⏸️ Sleep {delay:.1f}s")
                await page.wait_for_timeout(int(delay * 1000))

        print(f"\n📊 PHASE 1 DONE – NEED RETRY: {len(retry_queue)} documents")

        # -------- PHASE 2: RETRY --------
        if retry_queue:
            print(f"\n🔁 START RETRY...\n")

        still_retry = []

        for title, url in retry_queue:
            data = await check_document_status(page, title, url)

            if data["status"] == "RETRY":
                data["status"] = "UNKNOWN"
                still_retry.append(url)

            results.append(data)

            delay = random.uniform(*RETRY_DELAY_RANGE)
            print(f"⏸️ Retry sleep {delay:.1f}s")
            await page.wait_for_timeout(int(delay * 1000))

        print(f"\n📊 PHASE 2 DONE – STILL FAILED: {len(still_retry)} documents")

        await browser.close()

    # -------- OUTPUT --------
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        for r in results:
            f.write(
                f"[{r['status']}] {r['title']} | {r['so_hieu']} | "
                f"{r['ngay_hieu_luc']} | {r['url']}\n"
            )

    print("\n🎉 DONE – Output:", OUTPUT_TXT)

if __name__ == "__main__":
    asyncio.run(main())
