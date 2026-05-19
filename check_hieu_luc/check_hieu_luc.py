#!/usr/bin/env python3
import asyncio
import json
import random
import re
import sys
from datetime import datetime
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ======================
# CONFIG
# ======================
BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "status_cache.json"
COOKIES_FILE = BASE_DIR / "cookies.txt"
DATA_FILE = BASE_DIR / "data.json"
RATE_LIMIT_STATE_FILE = BASE_DIR / "rate_limit_state.json"

# "OTHER" thường là trạng thái chưa phân loại rõ (vd: thiếu cookie / trang trả dữ liệu chung),
# nên luôn recheck để có cơ hội chuyển sang status hợp lệ.
RECHECK_STATUSES = {"LOAD_ERROR", "NO_LUOCDO", "PARSE_ERROR", "OTHER", "FAILED_PERMANENT"}

# Những trạng thái này được xem là cache "chưa đáng tin", nên run sau sẽ thử lại
# và nếu crawl mới thành công thì ghi đè cache cũ.
RETRY_CACHE_STATUSES = RECHECK_STATUSES

PER_URL_SLEEP_RANGE = (1, 3)
RETRY_SLEEP_RANGE = (2, 6)
MAX_RETRIES_PER_URL = 2
BATCH_SIZE = 10
BATCH_COOLDOWN_RANGE = (20, 45)
MAX_CONSECUTIVE_FAIL = 2
GLOBAL_COOLDOWN_RANGE = (180, 360)
MAX_CIRCUIT_BREAKER_TRIPS = 2
MAX_URL_PER_RUN = 200
MAX_URL_PER_DAY = 200
MAX_BYTES_PER_DAY = 300 * 1024 * 1024

BLOCKED_HTTP_STATUSES = {403, 429}
BLOCKED_KEYWORDS = (
    "captcha",
    "recaptcha",
    "too many requests",
    "access denied",
    "forbidden",
    "xác minh",
    "xac minh",
    "không phải robot",
    "khong phai robot",
)

# ======================
# LOGGING
# ======================
def log(level, msg):
    # Print a timestamped log line.
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {msg}")

def info(msg):
    # Log an informational message.
    log("INFO", msg)

def warn(msg):
    # Log a warning message.
    log("WARN", msg)

def error(msg):
    # Log an error message.
    log("ERROR", msg)

def success(msg):
    # Log a success message.
    log("SUCCESS", msg)

# ======================
# UTILS
# ======================
def normalize(text: str) -> str:
    # Collapse whitespace into a single space.
    return " ".join(text.split()) if text else ""

def parse_date(text: str):
    # Parse dd/mm/YYYY dates when the source text is valid.
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y")
    except:
        return None

def extract_expired_date(text: str):
    # Extract the first date pattern from a status string.
    match = re.search(r'(\d{2}/\d{2}/\d{4})', text)
    if match:
        return match.group(1)
    return None

def load_json_safe(path):
    # Load JSON without failing the whole run on bad files.
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        warn(f"Failed to parse JSON file {path}: {e}")
        return {}

# ======================
# RATE LIMIT STATE
# ======================
def today_key():
    # Build the daily quota key.
    return datetime.now().strftime("%Y-%m-%d")

def load_rate_limit_state():
    # Load or reset the daily quota state.
    state = load_json_safe(RATE_LIMIT_STATE_FILE)
    if not isinstance(state, dict) or state.get("date") != today_key():
        return {
            "date": today_key(),
            "urls_processed_today": 0,
            "bytes_downloaded_today": 0,
            "last_blocked_reason": None,
        }
    state.setdefault("urls_processed_today", 0)
    state.setdefault("bytes_downloaded_today", 0)
    state.setdefault("last_blocked_reason", None)
    return state

def save_rate_limit_state(state):
    # Persist the daily quota state.
    with open(RATE_LIMIT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def check_rate_limits(state, urls_processed_this_run):
    # Stop the run when daily or per-run quotas are exhausted.
    if urls_processed_this_run >= MAX_URL_PER_RUN:
        return f"MAX_URL_PER_RUN reached ({MAX_URL_PER_RUN})"
    if state["urls_processed_today"] >= MAX_URL_PER_DAY:
        return f"MAX_URL_PER_DAY reached ({MAX_URL_PER_DAY})"
    if state["bytes_downloaded_today"] >= MAX_BYTES_PER_DAY:
        mb = MAX_BYTES_PER_DAY / (1024 * 1024)
        return f"MAX_BYTES_PER_DAY reached ({mb:.0f}MB)"
    return None

async def sleep_random(label, min_seconds, max_seconds):
    # Sleep for a randomized interval to reduce burst pressure.
    seconds = random.uniform(min_seconds, max_seconds)
    info(f"{label}: sleep {seconds:.1f}s")
    await asyncio.sleep(seconds)

# ======================
# BLOCK DETECTION
# ======================
async def is_blocked_page(page):
    # Detect simple anti-bot pages from visible body text.
    try:
        text = (await page.locator("body").inner_text(timeout=3000)).lower()
    except:
        return False, None

    for keyword in BLOCKED_KEYWORDS:
        if keyword in text:
            return True, f"blocked keyword detected: {keyword}"
    return False, None

# ======================
# COOKIE LOADING
# ======================
def load_cookies(path: Path):
    # Load cookies from Netscape or Playwright JSON format.
    if not path.exists():
        warn(f"Cookie file not found: {path}")
        return []

    # Playwright JSON cookies format
    if path.suffix.lower() == ".json":
        data = load_json_safe(path)
        return data if isinstance(data, list) else []

    # Netscape cookie file format
    cookies = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                cookies.append(
                    {
                        "domain": parts[0].lstrip("."),
                        "path": parts[2],
                        "secure": parts[3].upper() == "TRUE",
                        "name": parts[5],
                        "value": parts[6],
                    }
                )
    except Exception as e:
        warn(f"Load cookies failed: {e}")
        return []

    return cookies

# ======================
# CACHE SAVE
# ======================
def save_cache(cache):
    # Persist the crawl cache to disk.
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def make_replacement_record(
    *,
    normalized_status="",
    raw_status="",
    so_hieu="",
    expired_date=None,
    last_checked="",
    title="",
    url="",
    effective_date="",
    error="",
):
    # Build the canonical replacement object.
    return {
        "normalized_status": normalized_status,
        "raw_status": raw_status,
        "so_hieu": so_hieu,
        "expired_date": expired_date,
        "effective_date": effective_date,
        "last_checked": last_checked,
        "title": title,
        "url": url,
        "error": error,
    }

def normalize_cache_entry(entry):
    # Migrate older cache shapes into the current public response format.
    if not isinstance(entry, dict):
        entry = {}

    replacements = entry.get("replacements")
    if not isinstance(replacements, list):
        replacements = []

    normalized_status = entry.get("normalized_status", "UNKNOWN")
    raw_status = entry.get("raw_status", "")
    so_hieu = entry.get("so_hieu", "")
    expired_date = entry.get("expired_date")
    last_checked = entry.get("last_checked", "")

    normalized_replacements = []
    if replacements:
        for item in replacements:
            if not isinstance(item, dict):
                continue
            normalized_replacements.append(
                make_replacement_record(
                    normalized_status=item.get("normalized_status", normalized_status),
                    raw_status=item.get("raw_status", raw_status),
                    so_hieu=item.get("so_hieu", so_hieu),
                    expired_date=item.get("expired_date", expired_date),
                    last_checked=item.get("last_checked", last_checked),
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    effective_date=item.get("effective_date", ""),
                    error=item.get("error", ""),
                )
            )
    else:
        legacy_replacement = entry.get("replacment")
        if not isinstance(legacy_replacement, dict):
            legacy_replacement = entry.get("replacement")
        if not isinstance(legacy_replacement, dict):
            legacy_replacement = make_replacement_record(
                title=entry.get("replacement_title", ""),
                url=entry.get("replacement_url", ""),
                effective_date=entry.get("replacement_effective_date", ""),
                error=entry.get("replacement_error", ""),
            )

        if any(legacy_replacement.get(key) for key in ("title", "url", "effective_date", "error")):
            normalized_replacements.append(
                make_replacement_record(
                    normalized_status=normalized_status,
                    raw_status=raw_status,
                    so_hieu=so_hieu,
                    expired_date=expired_date,
                    last_checked=last_checked,
                    title=legacy_replacement.get("title", ""),
                    url=legacy_replacement.get("url", ""),
                    effective_date=legacy_replacement.get("effective_date", ""),
                    error=legacy_replacement.get("error", ""),
                )
            )

    normalized = {
        "normalized_status": normalized_status,
        "raw_status": raw_status,
        "so_hieu": so_hieu,
        "expired_date": expired_date,
        "replacements": normalized_replacements,
        "last_checked": last_checked,
    }

    if "error_type" in entry:
        normalized["error_type"] = entry.get("error_type")
    if "attempts" in entry:
        normalized["attempts"] = entry.get("attempts")

    return normalized

def compact_json(value):
    # Render JSON on one line for txt output.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def should_retry_cached_status(status: str) -> bool:
    # Retry cached documents only for unstable statuses.
    return status in RETRY_CACHE_STATUSES

def has_replacement_data(data: dict) -> bool:
    # Check whether an expired document already has replacement info.
    if data.get("normalized_status") != "EXPIRED":
        return True
    return bool(data.get("replacements"))

# ======================
# PHÂN LOẠI TÌNH TRẠNG
# ======================
def classify_status(raw_status: str):
    # Normalize the raw status text into a smaller status set.
    if not raw_status:
        return "PARSE_ERROR"

    text = raw_status.lower()

    if "chưa có hiệu lực" in text:
        return "NOT_EFFECTIVE_YET"

    if "còn hiệu lực" in text:
        return "VALID"

    if "hết hiệu lực" in text:
        return "EXPIRED"

    if "không còn phù hợp" in text:
        return "NOT_APPLICABLE"

    return "OTHER"

# ======================
# LƯỢC ĐỒ HELPERS
# ======================
async def open_luoc_do(page, url):
    # Open the document and switch to the Luoc Do tab.
    try:
        response = await page.goto(url, timeout=200000)
        await page.wait_for_load_state("domcontentloaded")
    except Exception as e:
        warn(f"Không load được trang: {e}")
        return None, {
            "normalized_status": "LOAD_ERROR",
            "raw_status": None,
            "error_type": "LOAD_ERROR",
        }

    if response and response.status in BLOCKED_HTTP_STATUSES:
        reason = f"HTTP {response.status}"
        error(f"BLOCKED detected: {reason}")
        return response, {
            "normalized_status": "BLOCKED",
            "raw_status": None,
            "error_type": reason,
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    blocked, blocked_reason = await is_blocked_page(page)
    if blocked:
        error(f"BLOCKED detected: {blocked_reason}")
        return response, {
            "normalized_status": "BLOCKED",
            "raw_status": None,
            "error_type": blocked_reason,
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    try:
        await page.wait_for_selector("#aLuocDo", timeout=20000)
        await page.click("#aLuocDo")
        await page.wait_for_selector("#cmDiagram", timeout=20000)
    except PlaywrightTimeoutError:
        blocked, blocked_reason = await is_blocked_page(page)
        if blocked:
            error(f"BLOCKED detected: {blocked_reason}")
            return response, {
                "normalized_status": "BLOCKED",
                "raw_status": None,
                "error_type": blocked_reason,
                "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        warn("Không load được tab Lược đồ")
        return response, {
            "normalized_status": "NO_LUOCDO",
            "raw_status": None,
            "error_type": "NO_LUOCDO",
        }

    return response, None

async def extract_viewing_document_metadata(page):
    # Read metadata fields from the viewingDocument container.
    container = await page.query_selector("#viewingDocument")
    if not container:
        return None

    raw_data = {}
    for att in await container.query_selector_all(".att"):
        k = await att.query_selector(".hd")
        v = await att.query_selector(".ds")
        if k and v:
            raw_data[
                normalize(await k.inner_text()).rstrip(":")
            ] = normalize(await v.inner_text())

    return raw_data

def build_snapshot_from_metadata(metadata, *, last_checked):
    # Convert raw metadata into the canonical per-URL snapshot.
    raw_status = metadata.get("Tình trạng", "")
    so_hieu = metadata.get("Số hiệu", "")
    effective_date = metadata.get("Ngày hiệu lực", "")
    normalized_status = classify_status(raw_status)
    expired_date = extract_expired_date(raw_status) if normalized_status == "EXPIRED" else None

    return {
        "normalized_status": normalized_status,
        "raw_status": raw_status,
        "so_hieu": so_hieu,
        "expired_date": expired_date,
        "effective_date": effective_date,
        "last_checked": last_checked,
    }

async def fetch_document_snapshot(page, url):
    # Crawl one URL and return its own status snapshot only.
    _, open_error = await open_luoc_do(page, url)
    if open_error:
        return None, open_error

    metadata = await extract_viewing_document_metadata(page)
    if metadata is None:
        return None, {
            "normalized_status": "PARSE_ERROR",
            "raw_status": None,
            "error_type": "PARSE_ERROR",
        }

    snapshot = build_snapshot_from_metadata(
        metadata,
        last_checked=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return snapshot, None

async def extract_replacement_documents(page, current_url):
    # Find the replacement-document box and return all valid links.
    header = await page.query_selector(
        "#cmDiagram .ghd.ghda[onclick*='replaceDocument']"
    )

    if not header:
        for candidate in await page.query_selector_all("#cmDiagram .ghd.ghda"):
            text = normalize(await candidate.inner_text())
            if "Văn bản thay thế" in text:
                header = candidate
                break

    if not header:
        return None, "NO_REPLACEMENT_BOX"

    try:
        await header.click()
        await page.wait_for_timeout(300)
    except Exception as e:
        warn(f"Không mở được box Văn bản thay thế: {e}")

    container = await page.query_selector("#replaceDocument")
    if not container:
        return None, "NO_REPLACEMENT_LINK"

    links = await container.query_selector_all("a[href]")
    current_normalized = current_url.rstrip("/")
    replacements = []
    seen_urls = set()

    for link in links:
        href = normalize(await link.get_attribute("href") or "")
        title = normalize(await link.inner_text())
        if not href:
            continue

        absolute_url = urljoin(page.url, href)
        if absolute_url.rstrip("/") == current_normalized:
            continue

        normalized_url = absolute_url.rstrip("/")
        if normalized_url in seen_urls:
            continue

        seen_urls.add(normalized_url)
        replacements.append(
            {
                "title": title,
                "url": absolute_url,
            }
        )

    if not replacements:
        return None, "NO_REPLACEMENT_LINK"

    return replacements, None

async def fetch_replacement_snapshot(page, replacement_url):
    # Crawl the replacement document and read its own snapshot.
    snapshot, error_result = await fetch_document_snapshot(page, replacement_url)
    if error_result:
        return None, error_result

    return snapshot, None

# ======================
# CHECK DOCUMENT
# ======================
async def check_document(page, title, url):
    # Crawl one document, then enrich expired ones with replacement data.
    info(f"Checking → {title}")

    result, open_error = await fetch_document_snapshot(page, url)
    if open_error:
        return open_error

    result.pop("effective_date", None)
    info(f"Raw status: {result['raw_status']}")
    info(f"Normalized: {result['normalized_status']}")

    result["replacements"] = []

    if result["normalized_status"] != "EXPIRED":
        return result

    replacement_docs, replacement_error = await extract_replacement_documents(page, url)
    if replacement_error:
        result["replacements"] = []
        warn(f"Không lấy được văn bản thay thế: {replacement_error}")
        return result

    replacements = []
    fatal_error = None

    for replacement in replacement_docs:
        info(f"Replacement → {replacement['title']} | {replacement['url']}")
        replacement_snapshot, snapshot_error = await fetch_replacement_snapshot(
            page,
            replacement["url"],
        )

        replacement_item = make_replacement_record(
            title=replacement["title"],
            url=replacement["url"],
            last_checked=result["last_checked"],
        )

        if snapshot_error:
            error_type = snapshot_error.get("error_type") or "REPLACEMENT_EFFECTIVE_DATE_NOT_FOUND"
            replacement_item["normalized_status"] = snapshot_error.get("normalized_status", "")
            replacement_item["raw_status"] = snapshot_error.get("raw_status") or ""
            replacement_item["so_hieu"] = snapshot_error.get("so_hieu", "")
            replacement_item["expired_date"] = snapshot_error.get("expired_date")
            replacement_item["effective_date"] = ""
            replacement_item["error"] = error_type

            if snapshot_error.get("normalized_status") == "BLOCKED":
                fatal_error = error_type
                replacements.append(replacement_item)
                break

            warn(f"Không lấy được Ngày hiệu lực văn bản thay thế: {error_type}")
            replacements.append(replacement_item)
            continue

        replacement_item["normalized_status"] = replacement_snapshot["normalized_status"]
        replacement_item["raw_status"] = replacement_snapshot["raw_status"]
        replacement_item["so_hieu"] = replacement_snapshot["so_hieu"]
        replacement_item["expired_date"] = replacement_snapshot["expired_date"]
        replacement_item["effective_date"] = replacement_snapshot["effective_date"]
        replacement_item["last_checked"] = replacement_snapshot["last_checked"]
        replacement_item["error"] = ""
        success(
            f"Replacement snapshot: {replacement_snapshot['normalized_status']} | "
            f"{replacement_snapshot['effective_date'] or replacement_snapshot['expired_date'] or ''}"
        )
        replacements.append(replacement_item)

    result["replacements"] = replacements

    if fatal_error:
        return {
            **result,
            "normalized_status": "BLOCKED",
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    return result

# Retry only transient failures; blocked pages are returned immediately.
# ======================
# RETRY WRAPPER
# ======================
async def check_document_with_retries(page, title, url):
    # Retry unstable document checks before giving up.
    attempts = 0
    last_result = None

    while attempts <= MAX_RETRIES_PER_URL:
        attempts += 1
        info(f"Attempt {attempts}/{MAX_RETRIES_PER_URL + 1} → {title}")
        result = await check_document(page, title, url)
        result["attempts"] = attempts
        last_result = result

        status = result.get("normalized_status")
        if status == "BLOCKED":
            return result
        if status not in RECHECK_STATUSES:
            return result

        if attempts <= MAX_RETRIES_PER_URL:
            warn(f"Retryable failure ({status}) → {title}")
            await sleep_random("Retry delay", *RETRY_SLEEP_RANGE)

    warn(f"FAILED_PERMANENT after {attempts} attempt(s) → {title}")
    return {
        "normalized_status": "FAILED_PERMANENT",
        "raw_status": last_result.get("raw_status") if last_result else None,
        "so_hieu": last_result.get("so_hieu", "") if last_result else "",
        "expired_date": None,
        "error_type": last_result.get("error_type") if last_result else "UNKNOWN",
        "attempts": attempts,
        "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

# ======================
# MAIN
# ======================
async def main():
    # Run the full crawl, then write summary files and cache.

    if len(sys.argv) != 2:
        print("Usage: python check_hieu_luc.py dd/mm/YYYY")
        sys.exit(1)

    input_date = parse_date(sys.argv[1])
    if not input_date:
        print("Sai định dạng ngày")
        sys.exit(1)

    documents = load_json_safe(DATA_FILE)
    cache = load_json_safe(CACHE_FILE)
    cache = {title: normalize_cache_entry(entry) for title, entry in cache.items()}
    rate_state = load_rate_limit_state()
    save_rate_limit_state(rate_state)

    # Chỉ giữ cache thuộc tập văn bản hiện tại để tránh số liệu bị nhiễu.
    stale_titles = [t for t in list(cache.keys()) if t not in documents]
    if stale_titles:
        for t in stale_titles:
            cache.pop(t, None)
        info(f"Removed stale cache entries: {len(stale_titles)}")
        save_cache(cache)

    if cache:
        save_cache(cache)

    info(f"Tổng văn bản: {len(documents)}")
    info(f"Đã có cache: {len(cache)}")
    info(
        "Daily quota: "
        f"{rate_state['urls_processed_today']}/{MAX_URL_PER_DAY} URLs, "
        f"{rate_state['bytes_downloaded_today']}/{MAX_BYTES_PER_DAY} bytes"
    )

    urls_processed_this_run = 0
    consecutive_fail = 0
    circuit_breaker_trips = 0
    blocked_reason = None
    stop_reason = None
    skipped_cached = []
    success_titles = []
    failed_titles = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        cookies = load_cookies(COOKIES_FILE)
        info(f"Loaded cookies: {len(cookies)}")
        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()
        traffic = {"bytes_downloaded": 0}

        # Approximate daily traffic using response headers so the byte quota
        # tracks real crawl volume across the run.
        def record_response_bytes(response):
            content_length = response.headers.get("content-length")
            if not content_length:
                return
            try:
                traffic["bytes_downloaded"] += int(content_length)
            except ValueError:
                return

        page.on("response", record_response_bytes)

        for title, url in documents.items():

            # ======================
            # RECHECK LOGIC
            # ======================
            if title in cache:
                old_status = cache[title].get("normalized_status")

                if not should_retry_cached_status(old_status) and has_replacement_data(cache[title]):
                    info(f"Skip (đã có trạng thái hợp lệ) → {title}")
                    skipped_cached.append((title, url, old_status))
                    continue
                elif old_status == "EXPIRED" and not has_replacement_data(cache[title]):
                    info(f"Rechecking expired document to fetch replacement → {title}")
                else:
                    info(f"Rechecking cached failed status → {title} [{old_status}]")

            limit_reason = check_rate_limits(rate_state, urls_processed_this_run)
            if limit_reason:
                stop_reason = limit_reason
                warn(f"LIMIT_REACHED: {limit_reason}")
                break

            bytes_before = traffic["bytes_downloaded"]
            result = await check_document_with_retries(page, title, url)
            bytes_used = max(0, traffic["bytes_downloaded"] - bytes_before)

            cache[title] = result
            save_cache(cache)

            urls_processed_this_run += 1
            rate_state["urls_processed_today"] += 1
            replacement_urls_processed = len(result.get("replacements", []))
            urls_processed_this_run += replacement_urls_processed
            rate_state["urls_processed_today"] += replacement_urls_processed
            rate_state["bytes_downloaded_today"] += bytes_used
            save_rate_limit_state(rate_state)

            status = result.get("normalized_status")
            if status in {"VALID", "EXPIRED", "NOT_APPLICABLE"}:
                success_titles.append((title, url, status))
            elif status in {"FAILED_PERMANENT", "LOAD_ERROR", "NO_LUOCDO", "PARSE_ERROR", "OTHER"}:
                failed_titles.append((title, url, status, result.get("error_type")))

            info(
                f"Progress: run={urls_processed_this_run}/{MAX_URL_PER_RUN}, "
                f"today={rate_state['urls_processed_today']}/{MAX_URL_PER_DAY}, "
                f"bytes+={bytes_used}"
            )

            if status == "BLOCKED":
                blocked_reason = result.get("error_type") or "BLOCKED"
                stop_reason = f"BLOCKED: {blocked_reason}"
                rate_state["last_blocked_reason"] = blocked_reason
                save_rate_limit_state(rate_state)
                error("BLOCKED: Dừng run, không retry/bypass. Vui lòng kiểm tra quyền truy cập, cookie, tài khoản hoặc IP.")
                break

            if status in RECHECK_STATUSES or status == "FAILED_PERMANENT":
                consecutive_fail += 1
                warn(f"Consecutive fail: {consecutive_fail}/{MAX_CONSECUTIVE_FAIL}")
            else:
                consecutive_fail = 0

            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                circuit_breaker_trips += 1
                if circuit_breaker_trips >= MAX_CIRCUIT_BREAKER_TRIPS:
                    stop_reason = "Circuit breaker stopped run after repeated consecutive failures"
                    error(stop_reason)
                    break
                warn(f"COOLDOWN: fail liên tục {consecutive_fail} lần")
                await sleep_random("Global cooldown", *GLOBAL_COOLDOWN_RANGE)
                consecutive_fail = 0

            if urls_processed_this_run % BATCH_SIZE == 0:
                await sleep_random("Batch cooldown", *BATCH_COOLDOWN_RANGE)

            await sleep_random("Per-url delay", *PER_URL_SLEEP_RANGE)

        await browser.close()

    # ======================
    # XUẤT KẾT QUẢ ĐẦY ĐỦ
    # ======================
    all_results = []
    expired_before = []

    for title in documents.keys():
        data = cache.get(title, {})
        status = data.get("normalized_status", "UNKNOWN")
        raw_status = data.get("raw_status", "")
        so_hieu = data.get("so_hieu", "")
        expired_date = data.get("expired_date")

        all_results.append(
            {
                "title": title,
                "so_hieu": so_hieu,
                "status": status,
                "raw_status": raw_status,
                "expired_date": expired_date,
                "replacements": data.get("replacements", []),
                "replacements_text": compact_json(data.get("replacements", [])),
                "last_checked": data.get("last_checked", ""),
            }
        )

        if status == "EXPIRED" and expired_date:
            d = parse_date(expired_date)
            if d and d < input_date:
                expired_before.append((title, data))

    date_str = input_date.strftime("%d_%m_%Y")
    all_results_file = BASE_DIR / f"all_status_{date_str}.txt"
    expired_file = BASE_DIR / f"expired_before_{date_str}.txt"

    with open(all_results_file, "w", encoding="utf-8") as f:
        for row in all_results:
            f.write(
                f"[{row['status']}] {row['title']} | {row.get('so_hieu','')} | "
                f"{row.get('raw_status','')} | {row.get('expired_date') or ''} | "
                f"replacements={row.get('replacements_text','')}\n"
            )

    with open(expired_file, "w", encoding="utf-8") as f:
        for title, data in expired_before:
            f.write(
                f"{title} | {data.get('so_hieu','')} | "
                f"{data.get('raw_status','')} | "
                f"replacements={compact_json(data.get('replacements', []))} \n"
            )

    stats = Counter(cache.get(t, {}).get("normalized_status") for t in documents.keys())

    info("========== SUMMARY ==========")
    for k, v in stats.items():
        info(f"{k}: {v}")
    if not expired_before:
        info(f"Không có văn bản hết hiệu lực trước {input_date.strftime('%d/%m/%Y')}")
    info(
        f"Run processed: {urls_processed_this_run}; "
        f"Daily URL quota used: {rate_state['urls_processed_today']}/{MAX_URL_PER_DAY}; "
        f"Daily bytes used: {rate_state['bytes_downloaded_today']}/{MAX_BYTES_PER_DAY}"
    )
    info(
        f"Run result: success={len(success_titles)}, "
        f"failed={len(failed_titles)}, skipped_cache={len(skipped_cached)}"
    )
    success(f"All status saved ({len(all_results)} record(s)) → {all_results_file}")
    if skipped_cached:
        info("Skipped cached URLs:")
        for title, url, status in skipped_cached:
            info(f"  - [{status}] {title} | {url}")
    if failed_titles:
        warn("Failed URLs:")
        for title, url, status, error_type in failed_titles:
            warn(f"  - [{status}] {title} | {error_type or ''} | {url}")
    if stop_reason:
        warn(f"Run stopped early: {stop_reason}")

    success(f"Expired list saved ({len(expired_before)} record(s)) → {expired_file}")
    success(f"Cache saved → {CACHE_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
