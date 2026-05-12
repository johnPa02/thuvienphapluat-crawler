#!/usr/bin/env python3
import asyncio
import json
import os
import re
import random

from playwright.async_api import async_playwright


# ======================
# CONFIG
# ======================
DATA_JSON = "data_all_law.json"
COOKIES_FILE = "cookies.txt"
DOWNLOAD_DIR = "downloads_pdf"
DELAY_RANGE = (0, 1)

PDF_BTN = "#ctl00_Content_ThongTinVB_filePDFHyperLink"
ORIGINAL_BTN = "#ctl00_Content_ThongTinVB_pdfHyperLink"
VN_BTN = "#ctl00_Content_ThongTinVB_vietnameseHyperLink"


# ======================
# UTILS
# ======================
def load_data():
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"📄 Load {len(data)} documents")
    return data


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
    print(f"🍪 Load {len(cookies)} cookies")
    return cookies


def safe_filename(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text


def file_already_exists(title: str) -> bool:
    """Check if any file with name == title.* exists"""
    base = safe_filename(title)
    for f in os.listdir(DOWNLOAD_DIR):
        if os.path.splitext(f)[0] == base:
            return True
    return False


async def try_download(page, selector, title):
    link = await page.query_selector(selector)
    if not link:
        return False

    async with page.expect_download(timeout=120000) as dl_info:
        await page.evaluate("(el) => el.click()", link)

    download = await dl_info.value

    ext = os.path.splitext(download.suggested_filename)[1]
    if not ext:
        ext = ".doc"

    filename = safe_filename(title) + ext
    path = os.path.join(DOWNLOAD_DIR, filename)
    await download.save_as(path)

    print(f"   🎉 Saved: {path}")
    return True


# ======================
# CORE DOWNLOAD
# ======================
async def download_one(page, title, url):
    base_name = safe_filename(title)

    if file_already_exists(title):
        print(f"🌐 SKIP (đã có): {title}")
        return "skipped"

    try:
        print(f"🌐 START: {title}")

        await page.goto(url, timeout=120000)
        await page.wait_for_load_state("domcontentloaded")

        # 1️⃣ PDF
        if await try_download(page, PDF_BTN, title):
            return "downloaded"

        # 2️⃣ Văn bản gốc
        if await try_download(page, ORIGINAL_BTN, title):
            return "downloaded"

        # 3️⃣ Văn bản tiếng Việt
        if await try_download(page, VN_BTN, title):
            return "downloaded"

        print("   ❌ Không có link tải hợp lệ")
        return "failed"

    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        return "failed"


# ======================
# MAIN
# ======================
async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    data = load_data()
    cookies = load_cookies_netscape(COOKIES_FILE)

    stats = {
        "total": len(data),
        "skipped": 0,
        "downloaded": 0,
        "failed": 0,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        context = await browser.new_context(accept_downloads=True)
        await context.add_cookies(cookies)
        page = await context.new_page()

        for i, (title, url) in enumerate(data.items(), 1):
            print(f"\n📌 [{i}/{len(data)}]")
            result = await download_one(page, title, url)
            stats[result] += 1

            delay = random.uniform(*DELAY_RANGE)
            await page.wait_for_timeout(int(delay * 1000))

        await browser.close()

    # ======================
    # SUMMARY
    # ======================
    existing_files = len(os.listdir(DOWNLOAD_DIR))

    print("\n" + "=" * 60)
    print("📊 TỔNG KẾT")
    print("=" * 60)
    print(f"📄 Tổng văn bản cần tải : {stats['total']}")
    print(f"📁 File đã có sẵn      : {stats['skipped']}")
    print(f"⬇️  File tải mới        : {stats['downloaded']}")
    print(f"❌ Lỗi / không tải được: {stats['failed']}")
    print(f"📂 Tổng file trong thư mục: {existing_files}")
    print("🎉 HOÀN TẤT")


if __name__ == "__main__":
    asyncio.run(main())
