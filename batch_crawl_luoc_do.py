#!/usr/bin/env python3
import asyncio
import csv
import os
import random

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ======================
# CONFIG
# ======================
URLS_FILE = "law/bhyt_27_luocdo.txt"
COOKIES_FILE = "cookies.txt"
OUTPUT_CSV = "documents_2.csv"
DELAY_RANGE = (2, 5)

CSV_FIELDS = [
    "document_number",
    "document_type",
    "issuing_authority",
    "title",
    "issued_date",
]


# ======================
# UTILS
# ======================
def load_urls():
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]
    print(f"📄 Đã load {len(urls)} URL")
    return urls


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
    print(f"🍪 Đã load {len(cookies)} cookies")
    return cookies


def normalize(text: str) -> str:
    return " ".join(text.split())


def write_csv(rows):
    if not rows:
        print("⚠️ Không có dữ liệu để ghi")
        return

    existing_numbers = set()
    if os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_numbers.add(row["document_number"])

    new_rows = [
        r for r in rows if r["document_number"] and r["document_number"] not in existing_numbers
    ]

    if not new_rows:
        print("ℹ️ Không có bản ghi mới")
        return

    write_header = not os.path.exists(OUTPUT_CSV)

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"💾 Đã ghi {len(new_rows)} dòng mới vào {OUTPUT_CSV}")


# ======================
# CORE CRAWLER
# ======================
async def crawl_one(page, url):
    try:
        print(f"🌐 Truy cập: {url}")
        await page.goto(url, timeout=120000)
        await page.wait_for_load_state("domcontentloaded")

        # Click tab Lược đồ
        await page.wait_for_selector("#aLuocDo", timeout=15000)
        await page.click("#aLuocDo")

        # Đợi dữ liệu render
        await page.wait_for_selector("#viewingDocument .att", timeout=15000)
        container = await page.query_selector("#viewingDocument")

        # Title
        title_el = await container.query_selector(".tt")
        title = normalize(await title_el.inner_text()) if title_el else ""

        raw = {}
        for att in await container.query_selector_all(".att"):
            k = await att.query_selector(".hd")
            v = await att.query_selector(".ds")
            if not k or not v:
                continue
            key = normalize((await k.inner_text()).rstrip(":"))
            val = normalize(await v.inner_text())
            raw[key] = val

        result = {
            "document_number": raw.get("Số hiệu", ""),
            "document_type": raw.get("Loại văn bản", ""),
            "issuing_authority": raw.get("Nơi ban hành", ""),
            "issued_date": raw.get("Ngày ban hành", ""),
            "title": title,
        }

        print(f"✅ OK: {result['document_number']}")
        return result

    except PlaywrightTimeoutError:
        print(f"⏰ Timeout: {url}")
    except Exception as e:
        print(f"❌ Lỗi {url}: {e}")

    return None


# ======================
# MAIN
# ======================
async def main():
    urls = load_urls()
    cookies = load_cookies_netscape(COOKIES_FILE)
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context()
        await context.add_cookies(cookies)

        page = await context.new_page()

        for i, url in enumerate(urls, 1):
            print(f"\n📌 [{i}/{len(urls)}]")
            data = await crawl_one(page, url)
            if data:
                results.append(data)

            delay = random.uniform(*DELAY_RANGE)
            print(f"⏸️ Nghỉ {delay:.1f}s")
            await page.wait_for_timeout(int(delay * 1000))

        await browser.close()

    write_csv(results)
    print("🎉 HOÀN TẤT")


if __name__ == "__main__":
    asyncio.run(main())
