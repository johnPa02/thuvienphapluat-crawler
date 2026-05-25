#!/usr/bin/env python3
"""
Pipeline hoàn chỉnh để crawl và xử lý văn bản pháp luật từ thuvienphapluat.vn
Sử dụng:
python pipeline.py <url> [--output FILE] [--cookies FILE] [--doc-name NAME]
Ví dụ:
python pipeline.py "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-47-2021-ND-CP-huong-dan-Luat-Doanh-nghiep-470561.aspx"
python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --output "luat_abc.txt" --doc-name "Luật ABC 2024"
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import argparse
import json
import os
import re
from html import unescape
import sys
from bs4 import BeautifulSoup, NavigableString, Tag
from playwright.sync_api import sync_playwright
from table_converter import is_data_table, process_tables_in_content
from urllib.parse import unquote, urljoin

def load_cookies_from_file(cookie_file: str) -> list:
    """
    Load cookies từ file Netscape format (cookies.txt).
    """
    cookies = []
    with open(cookie_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                domain = parts[0]
                if domain.startswith('.'):
                    domain = domain[1:]
                cookie = {
                    'name': parts[5],
                    'value': parts[6],
                    'domain': domain,
                    'path': parts[2],
                    'secure': parts[3].upper() == 'TRUE',
                    'httpOnly': False,
                }
                try:
                    expires = int(parts[4])
                    if expires > 0:
                        cookie['expires'] = expires
                except:
                    pass
                cookies.append(cookie)
    return cookies

def extract_doc_name_from_url(url: str) -> str:
    """
    Tự động trích xuất tên văn bản từ URL.
    """
    patterns = [
        r'Nghi-dinh-(\d+)-(\d+)-ND-CP',
        r'Luat-(\d+)-(\d+)-QH(\d+)',
        r'Thong-tu-(\d+)-(\d+)-TT-([A-Z]+)',
        r'Quyet-dinh-(\d+)-(\d+)-QD-([A-Z]+)',
        r'Nghi-quyet-(\d+)-(\d+)-NQ-([A-Z]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            if 'Nghi-dinh' in url:
                return f"Nghị định {match.group(1)}/{match.group(2)}/NĐ-CP"
            elif 'Luat' in url:
                return f"Luật {match.group(1)}/{match.group(2)}/QH{match.group(3)}"
            elif 'Thong-tu' in url:
                return f"Thông tư {match.group(1)}/{match.group(2)}/TT-{match.group(3)}"
            elif 'Quyet-dinh' in url:
                return f"Quyết định {match.group(1)}/{match.group(2)}/QĐ-{match.group(3)}"
            elif 'Nghi-quyet' in url:
                return f"Nghị quyết {match.group(1)}/{match.group(2)}/NQ-{match.group(3)}"
    return "Văn bản"

def crawl_html(url: str, cookie_file: str = None) -> str:
    """
    Crawl HTML từ URL với JavaScript rendering.
    """
    print(f"🌐 Đang crawl: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        if cookie_file and os.path.exists(cookie_file):
            cookies = load_cookies_from_file(cookie_file)
            context.add_cookies(cookies)
            print(f"🍪 Đã load {len(cookies)} cookies từ {cookie_file}")
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Chờ thêm để Cloudflare JS challenge hoàn thành nếu có
        page.wait_for_timeout(3000)
        title = page.title()
        if "just a moment" in title.lower():
            print("⚠️  Phát hiện Cloudflare challenge, đang chờ xử lý...")
            page.wait_for_timeout(10000)
        html = page.content()
        browser.close()
        return html

def extract_law_id_from_url(url: str = None) -> str:
    """Extract law/document id from a TVPL URL ending with -<id>.aspx."""
    if not url:
        return ""
    match = re.search(r'-(\d+)\.aspx(?:$|[?#])', url)
    return match.group(1) if match else ""

def extract_form_template_ids(content_div, base_url: str = None) -> list:
    """
    Extract tất cả form template IDs từ các element có class='clsBookmark_bm'
    Returns: List of tuples [(template_text, law_id, bookmark_id, element), ...]
    """
    form_templates = []
    seen_elements = set()
    seen_templates = set()
    
    def should_skip_template_text(text: str) -> bool:
        return bool(re.match(r'^\s*phụ\s+lục\b', text or '', re.I | re.U))

    def normalize_template_text(text: str) -> str:
        return re.sub(r'\s+', ' ', text or '').strip()

    # Tìm tất cả bookmark biểu mẫu. Ưu tiên onclick vì LawID/Bookmark_ID phải
    # lấy từ LS_Tip_Type_Bookmark_bm(...), không tự đoán từ name/title.
    bookmark_elements = []
    for element in content_div.find_all('a'):
        classes = element.get('class') or []
        name = element.get('name', '')
        onclick_attr = element.get('onclick', '')
        if (
            'clsBookmark_bm' in classes
            or re.search(r'^bieumau_', name or '', re.I)
            or 'LS_Tip_Type_Bookmark_bm' in onclick_attr
        ):
            bookmark_elements.append(element)
    
    for element in bookmark_elements:
        onclick_attr = element.get('onclick', '')
        
        # Extract LawID và Bookmark_ID từ onclick
        # Pattern: LS_Tip_Type_Bookmark_bm('516302','4444790')
        id_match = re.search(r"LS_Tip_Type_Bookmark_bm\(['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\)", onclick_attr)
        
        law_id = id_match.group(1) if id_match else ''
        bookmark_id = id_match.group(2) if id_match else ''
        template_text = normalize_template_text(element.get_text(' ', strip=True))

        if should_skip_template_text(template_text):
            continue

        template_key = (template_text, law_id, bookmark_id)

        if law_id and template_key not in seen_templates:
            form_templates.append((template_text, law_id, bookmark_id, element))
            seen_elements.add(id(element))
            seen_templates.add(template_key)

    direct_links = content_div.find_all('a', href=re.compile(r'files\.thuvienphapluat\.vn/uploads/DocForms/', re.I))
    for element in direct_links:
        if id(element) in seen_elements:
            continue
        template_text = normalize_template_text(element.get_text(' ', strip=True)) or 'Tải biểu mẫu'
        if should_skip_template_text(template_text):
            continue
        template_key = (template_text, '', '')
        if template_key in seen_templates:
            continue
        form_templates.append((template_text, '', '', element))
        seen_elements.add(id(element))
        seen_templates.add(template_key)

    download_links = content_div.find_all('a', attrs={'data-action': 'download'})
    for element in download_links:
        if id(element) in seen_elements:
            continue
        template_text = normalize_template_text(element.get_text(' ', strip=True)) or 'Tải biểu mẫu'
        if should_skip_template_text(template_text):
            continue
        template_key = (template_text, '', '')
        if template_key in seen_templates:
            continue
        form_templates.append((template_text, '', '', element))
        seen_elements.add(id(element))
        seen_templates.add(template_key)
    
    return form_templates

def extract_direct_form_url(element) -> str:
    """Extract direct URL if it already exists in HTML."""
    if not element:
        return ""

    href = element.get("href", "")
    if href and "files.thuvienphapluat.vn" in href:
        return unescape(href)

    download_link = element.find("a", attrs={"data-action": "download"})
    if download_link and download_link.get("href"):
        return unescape(download_link.get("href", ""))

    iframe = element.find("iframe")
    if iframe and iframe.get("src"):
        src = iframe.get("src", "")
        match = re.search(r"[?&]url=(https?://[^&]+)", src)
        if match:
            return unescape(unquote(match.group(1)))
        return src

    return ""

FORM_REQUEST_CONCURRENCY = 4
FORM_REQUEST_DELAY_RANGE = (0.0, 0.15)
FORM_BATCH_PAUSE_RANGE = (0.15, 0.5)
FORM_RATE_LIMIT_BACKOFF_BASE = 2.5
FORM_RATE_LIMIT_COOLDOWN = 4.0
FORM_SECOND_PASS_DELAY = 0.5

FORM_AJAX_STATS = {
    "count": 0,
    "ok": 0,
    "rate_limit": 0,
    "no_url": 0,
    "error": 0,
    "elapsed_ms": 0.0,
}

FORM_AJAX_CACHE = {}

def extract_download_url_from_ajax_response(response_text: str) -> str:
    """Parse the real DocForms URL from LoadBieuMau response HTML/text."""
    if not response_text:
        return ""

    full_match = re.search(
        r"https?://files\.thuvienphapluat\.vn/uploads/DocForms/[^\"'<>\r\n]+?\.(?:doc|docx|xls|xlsx|pdf)",
        response_text,
        re.I,
    )
    if full_match:
        return unescape(full_match.group(0))

    relative_match = re.search(
        r"/uploads/DocForms/[^\"'<>\r\n]+?\.(?:doc|docx|xls|xlsx|pdf)",
        response_text,
        re.I,
    )
    if relative_match:
        return "https://files.thuvienphapluat.vn" + unescape(relative_match.group(0))

    return ""

def resolve_form_template_url(page, template_text: str, law_id: str, bookmark_id: str, element=None, retry_count: int = 0) -> str:
    """Resolve form URL from LoadBieuMau response, then direct HTML fallback."""
    if law_id and bookmark_id:
        ajax_url = fetch_form_template_url(page, law_id, bookmark_id, template_text=template_text, retry_count=retry_count)
        if ajax_url:
            return ajax_url

    direct_url = extract_direct_form_url(element)
    if direct_url:
        return direct_url

    return ""

import random
import time

def is_rate_limited_response(response_text: str) -> bool:
    """Detect TVPL anti-flood responses from LoadBieuMau."""
    if not response_text:
        return False

    normalized = re.sub(r'\s+', ' ', response_text).lower()
    return any(
        marker in normalized
        for marker in (
            'tần suất quá nhiều',
            'tầng xuất quá nhiều',
            'truy cập với tần suất quá nhiều',
            'truy cập với tầng xuất quá nhiều',
            'too many',
            'rate limit',
        )
    )

def wait_between_form_requests(min_delay: float = None, max_delay: float = None) -> None:
    """Throttle form AJAX calls to avoid TVPL anti-flood limits."""
    min_delay = FORM_REQUEST_DELAY_RANGE[0] if min_delay is None else min_delay
    max_delay = FORM_REQUEST_DELAY_RANGE[1] if max_delay is None else max_delay
    time.sleep(random.uniform(min_delay, max_delay))

def wait_between_form_batches(min_delay: float = None, max_delay: float = None) -> None:
    """Small jitter between AJAX batches to avoid bursty traffic."""
    min_delay = FORM_BATCH_PAUSE_RANGE[0] if min_delay is None else min_delay
    max_delay = FORM_BATCH_PAUSE_RANGE[1] if max_delay is None else max_delay
    time.sleep(random.uniform(min_delay, max_delay))

def log_form_ajax_timing(template_text: str, law_id: str, bookmark_id: str, status: str, started_at: float, detail: str = "") -> None:
    """Print a compact timing line for each LoadBieuMau call."""
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    FORM_AJAX_STATS["count"] += 1
    FORM_AJAX_STATS["elapsed_ms"] += elapsed_ms
    if status == "ok":
        FORM_AJAX_STATS["ok"] += 1
    elif status.startswith("rate-limit"):
        FORM_AJAX_STATS["rate_limit"] += 1
    elif status == "no-url":
        FORM_AJAX_STATS["no_url"] += 1
    elif status.startswith("error"):
        FORM_AJAX_STATS["error"] += 1

    suffix = f" | {detail}" if detail else ""
    print(
        f"      ⏱️  AJAX {status}: {template_text} "
        f"[LawID={law_id}, Bookmark_ID={bookmark_id}] "
        f"{elapsed_ms:.0f}ms{suffix}"
    )

def print_form_ajax_summary() -> None:
    """Print aggregate timing for all form AJAX requests."""
    count = FORM_AJAX_STATS["count"]
    avg_ms = FORM_AJAX_STATS["elapsed_ms"] / count if count else 0.0
    print(
        "📊 AJAX SUMMARY: "
        f"calls={count}, ok={FORM_AJAX_STATS['ok']}, rate_limit={FORM_AJAX_STATS['rate_limit']}, "
        f"no_url={FORM_AJAX_STATS['no_url']}, error={FORM_AJAX_STATS['error']}, avg={avg_ms:.0f}ms"
    )

def parse_form_ajax_response(response_text: str) -> tuple:
    """Return (download_url, status, preview) from a LoadBieuMau response."""
    if not response_text:
        return "", "no-url", "empty response"

    response_preview = " ".join(response_text.strip().split())[:200]

    if is_rate_limited_response(response_text):
        return "", "rate-limit", response_preview

    if ('<title>Just a moment' in response_text or 'Cloudflare' in response_text):
        return "", "cloudflare", response_preview

    download_url = extract_download_url_from_ajax_response(response_text)
    if download_url:
        return download_url, "ok", download_url[:120]

    return "", "no-url", response_preview

def fetch_form_template_urls_batch(page, requests: list, concurrency: int = FORM_REQUEST_CONCURRENCY) -> list:
    """Fetch multiple LoadBieuMau URLs in parallel inside the page context."""
    if not requests:
        return []

    concurrency = max(1, min(int(concurrency or 1), len(requests)))
    return page.evaluate(
        """async ({ requests, concurrency }) => {
            const results = new Array(requests.length);
            let index = 0;

            async function worker() {
                while (true) {
                    const currentIndex = index++;
                    if (currentIndex >= requests.length) return;

                    const item = requests[currentIndex];
                    try {
                        const body = new URLSearchParams({
                            action: 'LoadBieuMau',
                            LawID: item.lawId,
                            Bookmark_ID: item.bookmarkId,
                        });

                        const res = await fetch('/page/ajaxcontroler.aspx', {
                            method: 'POST',
                            headers: {
                                'accept': '*/*',
                                'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                'x-requested-with': 'XMLHttpRequest',
                            },
                            body,
                            credentials: 'include',
                        });

                        results[currentIndex] = {
                            ok: true,
                            text: await res.text(),
                        };
                    } catch (error) {
                        results[currentIndex] = {
                            ok: false,
                            status: 'error',
                            error: String(error),
                        };
                    }
                }
            }

            await Promise.all(Array.from({ length: concurrency }, () => worker()));
            return results;
        }""",
        {"requests": requests, "concurrency": concurrency},
    )

def fetch_form_template_url(page, law_id: str, bookmark_id: str, template_text: str = "", retry_count: int = 0, max_retries: int = 5) -> str:
    """
    Gọi AJAX endpoint để lấy URL download của biểu mẫu.
    Returns: Full URL thật parse từ response, hoặc empty string nếu fail.
    """
    ajax_started_at = time.perf_counter()
    label = template_text or f"LawID={law_id}, Bookmark_ID={bookmark_id}"
    print(f"      ▶ AJAX start: {label}")

    cache_key = (law_id, bookmark_id)
    if cache_key in FORM_AJAX_CACHE:
        cached_url = FORM_AJAX_CACHE[cache_key]
        log_form_ajax_timing(label, law_id, bookmark_id, "cache", ajax_started_at, cached_url[:120])
        return cached_url

    try:
        response = page.evaluate(
            """async ({ lawId, bookmarkId }) => {
                const body = new URLSearchParams({
                    action: 'LoadBieuMau',
                    LawID: lawId,
                    Bookmark_ID: bookmarkId,
                });

                const res = await fetch('/page/ajaxcontroler.aspx', {
                    method: 'POST',
                    headers: {
                        'accept': '*/*',
                        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'x-requested-with': 'XMLHttpRequest',
                    },
                    body,
                    credentials: 'include',
                });

                return await res.text();
            }""",
            {"lawId": law_id, "bookmarkId": bookmark_id},
        )
        
        if not response:
            return ""
        
        download_url, status, detail = parse_form_ajax_response(response)

        if status == "rate-limit":
            if retry_count < max_retries:
                wait_seconds = FORM_RATE_LIMIT_BACKOFF_BASE * (1.5 ** retry_count) + random.uniform(0.2, 1.0)
                log_form_ajax_timing(label, law_id, bookmark_id, "rate-limit", ajax_started_at, f"sleep {wait_seconds:.1f}s then retry {retry_count + 1}/{max_retries}")
                time.sleep(wait_seconds)
                return fetch_form_template_url(page, law_id, bookmark_id, template_text=template_text, retry_count=retry_count + 1, max_retries=max_retries)

            log_form_ajax_timing(label, law_id, bookmark_id, "rate-limit-fail", ajax_started_at, detail)
            return ""

        if status == "cloudflare":
            if retry_count < max_retries:
                log_form_ajax_timing(label, law_id, bookmark_id, "cloudflare", ajax_started_at, f"retry {retry_count + 1}/{max_retries}")
                time.sleep(random.uniform(0.8, 2.0))
                return fetch_form_template_url(page, law_id, bookmark_id, template_text=template_text, retry_count=retry_count + 1, max_retries=max_retries)
            log_form_ajax_timing(label, law_id, bookmark_id, "cloudflare-fail", ajax_started_at, detail)
            return ""

        if download_url:
            FORM_AJAX_CACHE[cache_key] = download_url
            log_form_ajax_timing(label, law_id, bookmark_id, "ok", ajax_started_at, download_url[:120])
            return download_url

        log_form_ajax_timing(label, law_id, bookmark_id, "no-url", ajax_started_at, detail)
        return ""
        
    except Exception as e:
        if retry_count < max_retries:
            log_form_ajax_timing(label, law_id, bookmark_id, "error", ajax_started_at, str(e))
            time.sleep(random.uniform(0.8, 2.0))
            return fetch_form_template_url(page, law_id, bookmark_id, template_text=template_text, retry_count=retry_count + 1, max_retries=max_retries)
        log_form_ajax_timing(label, law_id, bookmark_id, "error-fail", ajax_started_at, str(e))
        return ""

def resolve_and_replace_form_template(page, template_text: str, law_id: str, bookmark_id: str, element) -> str:
    download_url = resolve_form_template_url(
        page,
        template_text,
        law_id,
        bookmark_id,
        element=element,
        retry_count=0,
    )

    if download_url:
        markdown_link = f" [{template_text}]({download_url})"
        element.replace_with(markdown_link)

    return download_url

def process_form_templates(page, content_div, base_url: str) -> dict:
    """
    Process all form templates và replace với markdown links.
    Returns: Dict mapping template text to download URL
    """
    print("📋 Đang xử lý biểu mẫu...")
    FORM_AJAX_STATS.update({"count": 0, "ok": 0, "rate_limit": 0, "no_url": 0, "error": 0, "elapsed_ms": 0.0})
    
    form_templates = extract_form_template_ids(content_div, base_url=base_url)
    template_urls = {}
    ajax_templates = []
    retry_templates = []

    for template_text, law_id, bookmark_id, element in form_templates:
        print(f"   🔄 Fetching: {template_text}")
        direct_url = extract_direct_form_url(element)
        if direct_url:
            markdown_link = f" [{template_text}]({direct_url})"
            element.replace_with(markdown_link)
            template_urls[template_text] = direct_url
            continue

        if law_id and bookmark_id:
            ajax_templates.append((template_text, law_id, bookmark_id, element))
        else:
            retry_templates.append((template_text, law_id, bookmark_id, element))

    random.shuffle(ajax_templates)

    if ajax_templates:
        pending = ajax_templates[:]
        attempt = 0
        while pending and attempt <= 2:
            if attempt > 0:
                time.sleep(FORM_SECOND_PASS_DELAY * attempt + random.uniform(0.0, 0.5))

            batch_size = FORM_REQUEST_CONCURRENCY if attempt == 0 else 1
            next_pending = []

            for start in range(0, len(pending), batch_size):
                batch = pending[start:start + batch_size]
                batch_payload = [
                    {"templateText": template_text, "lawId": law_id, "bookmarkId": bookmark_id}
                    for template_text, law_id, bookmark_id, _ in batch
                ]

                results = fetch_form_template_urls_batch(page, batch_payload, concurrency=min(batch_size, FORM_REQUEST_CONCURRENCY))
                for (template_text, law_id, bookmark_id, element), result in zip(batch, results):
                    if not result:
                        if attempt < 2:
                            next_pending.append((template_text, law_id, bookmark_id, element))
                        else:
                            print(f"   ⚠️  Bỏ qua: {template_text} (empty batch result)")
                        continue

                    if not result.get("ok"):
                        if attempt < 2:
                            next_pending.append((template_text, law_id, bookmark_id, element))
                        else:
                            print(f"   ⚠️  Bỏ qua: {template_text} ({result.get('error', 'error')})")
                        continue

                    response_text = result.get("text", "")
                    download_url, status, detail = parse_form_ajax_response(response_text)

                    if download_url:
                        FORM_AJAX_CACHE[(law_id, bookmark_id)] = download_url
                        markdown_link = f" [{template_text}]({download_url})"
                        element.replace_with(markdown_link)
                        template_urls[template_text] = download_url
                        continue

                    if status in {"rate-limit", "cloudflare"} and attempt < 2:
                        next_pending.append((template_text, law_id, bookmark_id, element))
                    else:
                        print(f"   ⚠️  Bỏ qua: {template_text} ({detail})")

                wait_between_form_batches()

            if not next_pending:
                break

            print(f"   ⏳ Retry {len(next_pending)} biểu mẫu bị chặn (lượt {attempt + 1})...")
            pending = next_pending
            attempt += 1

    for template_text, law_id, bookmark_id, element in retry_templates:
        print(f"   🔁 Retry direct fallback: {template_text}")
        time.sleep(FORM_SECOND_PASS_DELAY + random.uniform(0.0, 0.5))
        download_url = resolve_and_replace_form_template(page, template_text, law_id, bookmark_id, element)

        if download_url:
            template_urls[template_text] = download_url
        else:
            print(f"   ⚠️  Bỏ qua sau retry: {template_text}")
    
    print_form_ajax_summary()
    return template_urls
def save_form_template_urls(template_urls: dict, output_file: str):
    """
    Save form template URLs to JSON file.
    """
    if template_urls:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(template_urls, f, ensure_ascii=False, indent=2)
        print(f"💾 Đã lưu {len(template_urls)} form template URLs vào: {output_file}")

def extract_hover_content(soup: BeautifulSoup, element) -> str:
    """
    Trích xuất nội dung hover tooltip từ element.
    """
    tooltip_class = None
    if element.get('atmm'):
        tooltip_class = element.get('atmm').strip('.')
    elif element.get('onmouseover'):
        match = re.search(r"['\"]\.([^'\"]+)['\"]", element.get('onmouseover'))
        if match:
            tooltip_class = match.group(1)
    
    if not tooltip_class:
        return ""
    
    tooltip_div = soup.find('div', class_=tooltip_class)
    if tooltip_div:
        tooltip_text = tooltip_div.get_text(separator=' ', strip=True)
        if tooltip_text and tooltip_text != "Click vào để xem nội dung":
            return f" [{tooltip_text}]"
    return ""

def extract_note_content(soup: BeautifulSoup, element) -> str:
    """
    Trích xuất nội dung từ dvNoteDieuKhoan dựa vào id của element.
    """
    element_id = element.get('id', '')
    if element_id.startswith('span-'):
        note_id = element_id[5:]  # Bỏ "span-"
    else:
        return ""
    
    note_div = soup.find('div', id=note_id)
    if note_div:
        note_text = note_div.get_text(separator=' ', strip=True)
        if note_text:
            parts = note_text.split('|~|')
            if len(parts) >= 2:
                main_content = parts[0].strip()
                source_note = parts[1].strip() if len(parts) > 1 else ""
                if source_note:
                    return f"\n{main_content} [{source_note}]"
                return f"\n{main_content}"
        return f"\n{note_text}"
    return ""

def normalize_image_url(src: str, base_url: str = None) -> str:
    """Normalize image URLs before writing them as markdown images."""
    if not src:
        return ""

    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http://") or src.startswith("https://"):
        return src
    if base_url:
        return urljoin(base_url, src)
    return src

def render_dom_children(node, base_url: str = None) -> str:
    return "".join(render_dom_node(child, base_url) for child in getattr(node, "children", []))

def render_math_node(node, base_url: str = None) -> str:
    """Render sub/sup as LaTeX-like fragments."""
    inner = render_dom_children(node, base_url)
    if node.name == "sub":
        return f"_{{{inner}}}" if inner else ""
    if node.name == "sup":
        return f"^{{{inner}}}" if inner else ""
    return inner

def render_dom_node(node, base_url: str = None) -> str:
    """Render content DOM to text while preserving images and formula structure."""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    name = (node.name or "").lower()
    if name in {"script", "style"}:
        return ""
    if name == "br":
        return "\n"
    if name == "img":
        img_url = normalize_image_url(node.get("src", ""), base_url)
        return f"![]({img_url})" if img_url else ""
    if name in {"sub", "sup"}:
        return render_math_node(node, base_url)

    text = render_dom_children(node, base_url)
    if name in {"p", "div", "section", "article", "blockquote", "center", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}:
        return text + "\n"
    return text

def render_content_div(content_div, base_url: str = None) -> str:
    rendered = render_dom_children(content_div, base_url)
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()

def normalize_formula_text(line: str) -> str:
    """Normalize multiplication only on formula-like lines."""
    if '_{' not in line and '^{' not in line and '=' not in line:
        return line
    return re.sub(r'\s*\*\s*', r' \\times ', line)

def normalize_appendix_breaks(text: str) -> str:
    """Ensure PHỤ LỤC headings are separated from previous content.

    The DOM renderer can flatten visually separated appendices into the
    previous paragraph/signature text. Use a blank line before PHỤ LỤC so both
    the raw crawl output and later chunking treat it as a hard boundary.
    """
    if not text:
        return text

    appendix_heading = r'(PHỤ\s+LỤC\s+(?:[IVXLCDM]+|\d+)\b)'
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'([^\n])\s+' + appendix_heading, r'\1\n\n\2', text, flags=re.I | re.U)
    text = re.sub(r'\n+[ \t]*' + appendix_heading, r'\n\n\1', text, flags=re.I | re.U)
    text = re.sub(r'\n{3,}(?=PHỤ\s+LỤC\s+(?:[IVXLCDM]+|\d+)\b)', '\n\n', text, flags=re.I | re.U)
    # Unnumbered PHỤ LỤC (e.g. "PHỤ LỤC MỘT SỐ BIỂU MẪU...") merged onto previous line.
    # Use strict case (no re.I) so lowercase "phụ lục" inside normal sentences is untouched.
    text = re.sub(r'([^\n]) +(PHỤ LỤC\b)', r'\1\n\n\2', text, flags=re.U)
    return text

def process_element_with_hover(soup: BeautifulSoup, content_div) -> None:
    """
    Xử lý các element có hover và chèn nội dung tooltip vào sau text.
    """
    hover_elements = content_div.find_all(attrs={'atmm': True})
    hover_elements += content_div.find_all(attrs={'onmouseover': re.compile(r'lqhlTootip', re.I)})
    
    seen = set()
    unique_elements = []
    for el in hover_elements:
        if id(el) not in seen:
            seen.add(id(el))
            unique_elements.append(el)
    
    for element in unique_elements:
        hover_content = extract_hover_content(soup, element)
        if hover_content:
            element.append(hover_content)
    
    # Xử lý các element <huongdan> với id="span-note_..."
    huongdan_elements = content_div.find_all('huongdan', id=re.compile(r'^span-note_'))
    for element in huongdan_elements:
        note_content = extract_note_content(soup, element)
        if note_content:
            element.string = note_content

def extract_content(html: str, url: str = None, page=None) -> tuple:
    """
    Trích xuất nội dung text từ HTML.
    Returns: (text_content, form_template_urls_dict)
    """
    print("📄 Đang trích xuất nội dung...")
    
    soup = BeautifulSoup(html, "html.parser")

    # Kiểm tra Cloudflare block
    page_title = soup.find("title")
    if page_title and "just a moment" in page_title.text.lower():
        raise ValueError(
            "Trang bị Cloudflare chặn (Just a moment...). "
            "Hãy cập nhật cookie 'cf_clearance' từ trình duyệt rồi thử lại."
        )

    content_div = soup.find("div", class_="content1")
    if content_div is None:
        # Thử các selector thay thế
        content_div = (
            soup.find("div", class_="content2")
            or soup.find("div", id="toanvan")
            or soup.find("div", class_=lambda c: c and "fulltext" in c)
        )
    if content_div is None:
        raise ValueError("Không tìm thấy thẻ <div class='content1'> trên trang")
    
    # Xử lý hover tooltips
    process_element_with_hover(soup, content_div)
    
    # Xử lý form templates (BIỂU MẪU) - CẦN PAGE ĐỂ GỌI AJAX
    form_template_urls = {}
    if page:
        form_template_urls = process_form_templates(page, content_div, url)
    
    # Xử lý các thẻ <b> chứa "Điều X." để tách tên điều và nội dung
    DIEU_MARKER = "<<<DIEU_NEWLINE>>>"
    for b_tag in content_div.find_all('b'):
        text_content = b_tag.get_text()
        if re.match(r'^Điều\s+\d+\.', text_content):
            normalized_text = ' '.join(text_content.split())
            b_tag.string = normalized_text
            b_tag.insert_after(NavigableString(DIEU_MARKER))
    
    # Xử lý các bảng
    print("🔄 Đang chuyển đổi bảng thành Markdown...")
    markdown_tables = []
    table_placeholders = []
    
    tables = content_div.find_all('table')
    for idx, table in enumerate(tables):
        if is_data_table(table):
            from table_converter import convert_table_to_markdown
            markdown = convert_table_to_markdown(table, base_url=url)
            markdown_tables.append(markdown)
            placeholder = f"<<<TABLE_{idx}>>>"
            table_placeholders.append(placeholder)
            placeholder_div = BeautifulSoup('<div></div>', 'html.parser').div
            placeholder_div.string = placeholder
            table.replace_with(placeholder_div)
    
    # Render DOM thay vì get_text() để không làm mất <img>, <sub>, <sup>.
    text = render_content_div(content_div, base_url=url)
    text = text.replace(DIEU_MARKER, '\n')
    text = normalize_appendix_breaks(text)
    
    # Chuẩn hóa dòng
    lines = text.split('\n')
    result = []
    buffer = ""
    
    new_paragraph_patterns = [
        r'^Mục\s+\d+',
        r'^Điều\s+\d+',
        r'^\d+\.\s',
        r'^[a-zđ]\)\s',
        r'^-\s',
        r'^PHỤ LỤC',
        r'^NGHỊ ĐỊNH',
        r'^Căn cứ',
        r'^Theo đề nghị',
        r'^Nơi nhận:',
        r'^TM\.',
        r'^CỘNG HÒA',
        r'^CHÍNH PHỦ',
        r'^Số:',
        r'^Hà Nội,',
        r'^Biểu số',
        r'^BẢNG',
        r'^TT$',
        r'^I\.\s',
        r'^II\.\s',
        r'^III\.\s',
        r'^IV\.\s',
        r'^V\.\s',
        r'^VI\.\s',
        r'^\s*\[\d+\]\s*'
    ]
    
    dieu_title_end_pattern = r'Điều\s+\d+\.\s+[^ ]+$'
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line = normalize_formula_text(line)
        
        is_new_paragraph = any(re.match(p, line) for p in new_paragraph_patterns)
        
        if is_new_paragraph:
            if buffer:
                result.append(buffer)
                buffer = line
            else:
                buffer = line
        else:
            if buffer:
                if re.search(dieu_title_end_pattern, buffer):
                    result.append(buffer)
                    buffer = line
                elif re.search(r'[.;?!]$', buffer):
                    result.append(buffer)
                    buffer = line
                else:
                    buffer = buffer + " " + line
            else:
                buffer = line
    
    if buffer:
        result.append(buffer)
    
    final_text = '\n'.join(result)
    final_text = normalize_appendix_breaks(final_text)
    
    for placeholder, markdown in zip(table_placeholders, markdown_tables):
        markdown_with_spacing = f"\n{markdown.strip()}"
        final_text = final_text.replace(placeholder, markdown_with_spacing)

    final_text = normalize_appendix_breaks(final_text)
    
    return final_text, form_template_urls

def postprocess(content: str, doc_name: str) -> str:
    """
    Postprocess văn bản pháp luật.
    """
    print("✨ Đang postprocess...")
    
    content = re.sub(r'\n\.\n', '\n', content)
    content = content.replace(' [Click vào để xem nội dung]', '')
    content = content.replace('[Click vào để xem nội dung]', '')
    
    content = re.sub(r'(Mục\s+\d+\.)', rf'\n{doc_name}. \1', content)
    content = normalize_appendix_breaks(content)
    
    content = re.sub(r'[""\u201c\u201d]\s*\n+\s*(Điều)', r'"\1', content)
    
    pattern = r'([^"\n""\u201c\u201d])(?!(?:' + re.escape(doc_name) + r'\.\s*)?)(Điều\s+\d+\.[ \t]+[A-ZĐÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ][a-zđàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+)'
    replacement = rf'\1\n{doc_name}. \2'
    content = re.sub(pattern, replacement, content)
    
    content = re.sub(r'^(?!(?:' + re.escape(doc_name) + r'\.\s*)?)(Điều\s+\d+\.[ \t]+[A-ZĐÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸ][a-zđàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+)', rf'{doc_name}. \1', content, flags=re.MULTILINE)
    
    content = re.sub(r'["\u201c\u201d]' + re.escape(doc_name) + r'\. (Điều)', r'"\1', content)
    
    content = re.sub(r'\n(' + re.escape(doc_name) + r'\. Điều)', r'\n\1', content)
    
    content = normalize_appendix_breaks(content)
    content = re.sub(r'\n{3,}', r'\n\n', content)
    content = normalize_appendix_breaks(content)
    content = content.lstrip('\n')
    
    return content

def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to be safe for filesystem.
    """
    # Replace unsafe characters
    filename = filename.replace('/', '_').replace('\\', '_')
    filename = filename.replace(':', '_').replace('*', '_')
    filename = filename.replace('?', '_').replace('"', '_')
    filename = filename.replace('<', '_').replace('>', '_').replace('|', '_')
    return filename

def run_pipeline(url: str, cookie_file: str = "cookies.txt", doc_name: str = None, output_dir: str = ".") -> str:
    """
    Chạy pipeline hoàn chỉnh.
    """
    print("=" * 60)
    print("🚀 THUVIENPHAPLUAT CRAWLER PIPELINE")
    print("=" * 60)
    
    # Auto-detect doc name
    if not doc_name:
        doc_name = extract_doc_name_from_url(url)
    print(f"📋 Văn bản: {doc_name}")
    
    # Create output directory if needed
    if output_dir and output_dir != ".":
        os.makedirs(output_dir, exist_ok=True)
    
    print("🌐 Đang crawl...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        
        if cookie_file and os.path.exists(cookie_file):
            cookies = load_cookies_from_file(cookie_file)
            context.add_cookies(cookies)
            print(f"🍪 Đã load {len(cookies)} cookies từ {cookie_file}")
        
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1000)
        
        html = page.content()
        print(f"   ✓ Đã tải {len(html):,} bytes HTML")
        
        # Extract content với form templates (cần page object)
        content, form_template_urls = extract_content(html, url=url, page=page)
        print(f"   ✓ Đã trích xuất {len(content):,} ký tự")
        
        browser.close()
    
    # Postprocess
    processed = postprocess(content, doc_name)
    print(f"   ✓ Đã postprocess xong")
    
    # Thêm doc_name vào đầu file
    processed = f"{doc_name}\n{processed}"
    
    # Generate output filename
    safe_doc_name = sanitize_filename(doc_name)
    output_file = os.path.join(output_dir, f"{safe_doc_name}.txt") if output_dir else f"{safe_doc_name}.txt"
    
    # Save main content
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(processed)
    print(f"   ✓ Đã lưu vào: {output_file}")
    
    # Save form template URLs to JSON
    if form_template_urls:
        data_url_file = os.path.join(output_dir, f"{safe_doc_name}_data_url.json") if output_dir else f"{safe_doc_name}_data_url.json"
        save_form_template_urls(form_template_urls, data_url_file)
    
    print("=" * 60)
    print("✅ HOÀN THÀNH!")
    print("=" * 60)
    
    return processed

def main():
    parser = argparse.ArgumentParser(
        description="Crawl và xử lý văn bản pháp luật từ thuvienphapluat.vn",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
python pipeline.py "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-47-2021-ND-CP-huong-dan-Luat-Doanh-nghiep-470561.aspx"
python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --output "output.txt"
python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --doc-name "Luật ABC 2024"
"""
    )
    parser.add_argument("url", help="URL của văn bản pháp luật trên thuvienphapluat.vn")
    parser.add_argument("-c", "--cookies", default="cookies.txt", help="File cookies (default: cookies.txt)")
    parser.add_argument("-n", "--doc-name", help="Tên văn bản (auto-detect nếu không cung cấp)")
    parser.add_argument("-o", "--output-dir", default=".", help="Output directory (default: current directory)")
    args = parser.parse_args()
    
    try:
        run_pipeline(
            url=args.url,
            cookie_file=args.cookies,
            doc_name=args.doc_name,
            output_dir=args.output_dir
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Lỗi: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
