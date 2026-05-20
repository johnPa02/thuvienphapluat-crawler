#!/usr/bin/env python3
import asyncio
import hashlib
import json
import random
import re
import sys
from datetime import datetime
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

if __package__ in {None, ""}:
    # Support running as a script: `python check_hieu_luc/check_hieu_luc.py ...`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patchright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

try:
    from .related_documents import RELATED_DOCUMENT_SECTIONS, collect_related_documents
except ImportError:
    from check_hieu_luc.related_documents import RELATED_DOCUMENT_SECTIONS, collect_related_documents

# ======================
# CONFIG
# ======================
BASE_DIR          = Path(__file__).resolve().parent
CACHE_DIR         = BASE_DIR / "cache"
CACHE_INDEX       = CACHE_DIR / "index.json"
LEGACY_CACHE_FILE = BASE_DIR / "status_cache.json"
COOKIES_FILE      = BASE_DIR / "cookies.txt"
DATA_FILE = BASE_DIR / "data.json"
RATE_LIMIT_STATE_FILE = BASE_DIR / "rate_limit_state.json"

# Khi DEV_MODE=True: mở browser có giao diện để giải captcha thủ công thay vì chỉ cooldown
DEV_MODE = False

# "OTHER" thường là trạng thái chưa phân loại rõ (vd: thiếu cookie / trang trả dữ liệu chung),
# nên luôn recheck để có cơ hội chuyển sang status hợp lệ.
RECHECK_STATUSES = {"LOAD_ERROR", "NO_LUOCDO", "PARSE_ERROR", "OTHER", "FAILED_PERMANENT"}

# Những trạng thái này được xem là cache "chưa đáng tin", nên run sau sẽ thử lại
# và nếu crawl mới thành công thì ghi đè cache cũ.
RETRY_CACHE_STATUSES = RECHECK_STATUSES

PER_URL_SLEEP_RANGE = (3, 7)
RELATED_URL_SLEEP_RANGE = (5, 12)
RELATED_RETRY_SLEEP_RANGE = (30, 60)
RETRY_SLEEP_RANGE = (5, 12)
MAX_RETRIES_PER_URL = 2
MAX_RETRIES_PER_RELATED_URL = 2
LUOCDO_WAIT_TIMEOUT_MS = 30000
LUOCDO_RETRY_ATTEMPTS = 3
LUOCDO_RELOAD_ATTEMPTS = 1
BATCH_SIZE = 5
BATCH_COOLDOWN_RANGE = (60, 120)
MAX_CONSECUTIVE_FAIL = 2
GLOBAL_COOLDOWN_RANGE = (180, 360)
MAX_CIRCUIT_BREAKER_TRIPS = 2
MAX_URL_PER_RUN = 200
MAX_URL_PER_DAY = 1000
MAX_BYTES_PER_DAY = 300 * 1024 * 1024
MAX_DEEP = 1
ENABLE_RELATED_DOCUMENTS = True
DEDUP_RELATED_URLS = True
PRESERVE_SOURCE_SECTION = True
LOG_RELATED_DOCUMENT_MAPPING = True
MAX_RELATED_URLS_PER_DOCUMENT = 50
ALLOWED_DOMAINS = ("thuvienphapluat.vn",)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
BROWSER_VIEWPORT = {"width": 1366, "height": 900}
BROWSER_LOCALE = "vi-VN"
BROWSER_TIMEZONE = "Asia/Ho_Chi_Minh"

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
VERIFICATION_KEYWORDS = (
    "xác minh",
    "xac minh",
    "không phải robot",
    "khong phai robot",
)

INVALID_ACCOUNT_ERROR_CODE = "INVALID_COOKIE_OR_NON_PRO_ACCOUNT"
INVALID_ACCOUNT_ERROR_MESSAGE = (
    "Cookie/tài khoản không hợp lệ hoặc tài khoản chưa lên Pro. "
    "Vui lòng kiểm tra lại cookie đăng nhập."
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


class InvalidAccountStateError(RuntimeError):
    # Raised when the site signals the logged-in account cannot access Pro data.
    def __init__(self, message=INVALID_ACCOUNT_ERROR_MESSAGE):
        super().__init__(message)
        self.error_code = INVALID_ACCOUNT_ERROR_CODE
        self.message = message

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

def atomic_write_json(path, value):
    # Avoid partially-written state files if the crawler is interrupted.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)

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
    atomic_write_json(RATE_LIMIT_STATE_FILE, state)

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
    # Detect anti-bot pages, but prefer real document metadata when it is present.
    try:
        container = await page.query_selector("#viewingDocument")
        if container:
            # Trang có #viewingDocument → đây là trang văn bản, không phải captcha.
            # Kiểm tra metadata nếu có để confirm, nhưng không dùng body text để detect block
            # vì từ "xác minh" xuất hiện rất nhiều trong nội dung pháp luật.
            keys = []
            for att in await container.query_selector_all(".att"):
                key_el = await att.query_selector(".hd")
                val_el = await att.query_selector(".ds")
                if not key_el or not val_el:
                    continue
                key = normalize((await key_el.inner_text()).rstrip(":"))
                value = normalize(await val_el.inner_text())
                if key:
                    keys.append(key)
                if key in {"Tình trạng", "Ngày hiệu lực", "Số hiệu"} and value:
                    return None, None
            if any(key in {"Tình trạng", "Ngày hiệu lực", "Số hiệu"} for key in keys):
                return None, None
            # #viewingDocument tồn tại nhưng không có metadata → trang đã load,
            # chỉ là thiếu thông tin (cần Pro). Không phải captcha page.
            return None, None
    except:
        pass

    # #viewingDocument không tồn tại → có thể là captcha/block page.
    # Chỉ dùng các keyword đặc thù của captcha, không dùng "xác minh" chung.
    try:
        text = (await page.locator("body").inner_text(timeout=3000)).lower()
    except:
        return None, None

    for keyword in BLOCKED_KEYWORDS:
        if keyword in text:
            if keyword in VERIFICATION_KEYWORDS:
                return "XAC_MINH", f"xác minh required: {keyword}"
            return "BLOCKED", f"blocked keyword detected: {keyword}"
    return None, None

async def has_viewing_document_metadata(page):
    # Treat the page as usable if core metadata fields are already rendered.
    try:
        container = await page.query_selector("#viewingDocument")
        if not container:
            return False
        for att in await container.query_selector_all(".att"):
            key_el = await att.query_selector(".hd")
            val_el = await att.query_selector(".ds")
            if not key_el or not val_el:
                continue
            key = normalize((await key_el.inner_text()).rstrip(":"))
            value = normalize(await val_el.inner_text())
            if key in {"Tình trạng", "Ngày hiệu lực", "Số hiệu"} and value:
                return True
        return False
    except:
        return False

async def wait_for_luoc_do_ready(page):
    # The Luoc Do tab is sometimes slow; retry before declaring NO_LUOCDO.
    last_error = None
    for reload_attempt in range(0, LUOCDO_RELOAD_ATTEMPTS + 1):
        if reload_attempt:
            warn(f"Reload trang để chờ lại tab Lược đồ ({reload_attempt}/{LUOCDO_RELOAD_ATTEMPTS})")
            await page.reload(timeout=200000)
            await page.wait_for_load_state("domcontentloaded")

        for attempt in range(1, LUOCDO_RETRY_ATTEMPTS + 1):
            try:
                await page.wait_for_selector("#aLuocDo", timeout=LUOCDO_WAIT_TIMEOUT_MS)
                await page.wait_for_timeout(random.randint(500, 1200))
                await page.mouse.wheel(0, random.randint(150, 500))
                await page.wait_for_timeout(random.randint(300, 900))
                await page.click("#aLuocDo")
                await page.wait_for_timeout(500)
                await page.wait_for_selector("#cmDiagram", timeout=LUOCDO_WAIT_TIMEOUT_MS)
                return True
            except PlaywrightTimeoutError as exc:
                last_error = exc
                if await has_viewing_document_metadata(page):
                    warn("Tab Lược đồ load chậm nhưng metadata đã sẵn sàng; tiếp tục dùng metadata hiện có")
                    return False
                warn(f"Lược đồ chưa sẵn sàng, retry {attempt}/{LUOCDO_RETRY_ATTEMPTS}")
                await page.wait_for_timeout(1000)
            except Exception as exc:
                last_error = exc
                if await has_viewing_document_metadata(page):
                    warn("Không click được tab Lược đồ nhưng metadata đã sẵn sàng; tiếp tục dùng metadata hiện có")
                    return False
                warn(f"Lỗi mở tab Lược đồ, retry {attempt}/{LUOCDO_RETRY_ATTEMPTS}: {exc}")
                await page.wait_for_timeout(1000)

        if await has_viewing_document_metadata(page):
            warn("Không load được tab Lược đồ sau retry nhưng metadata văn bản vẫn đọc được")
            return False
    raise last_error or PlaywrightTimeoutError("NO_LUOCDO")

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
# CACHE (per-document files + index)
# ======================
def url_to_slug(url: str) -> str:
    path = urlparse(url).path
    name = path.rstrip("/").rsplit("/", 1)[-1]
    slug = name.rsplit(".", 1)[0] if "." in name else name
    return slug or hashlib.md5(url.encode()).hexdigest()[:16]

def _slug_path(slug: str) -> Path:
    return CACHE_DIR / f"{slug}.json"

def load_cache_index() -> dict:
    return load_json_safe(CACHE_INDEX)

def save_cache_index(index: dict):
    CACHE_DIR.mkdir(exist_ok=True)
    atomic_write_json(CACHE_INDEX, index)

def load_per_document_cache(index: dict, documents: dict) -> dict:
    cache = {}
    for title in documents:
        slug = index.get(title)
        if not slug:
            continue
        entry = load_json_safe(_slug_path(slug))
        if entry:
            cache[title] = normalize_cache_entry(entry)
    return cache

def save_document_cache(index: dict, title: str, url: str, entry: dict):
    CACHE_DIR.mkdir(exist_ok=True)
    slug = index.get(title)
    if not slug:
        slug = url_to_slug(url)
        index[title] = slug
        save_cache_index(index)
    atomic_write_json(_slug_path(slug), entry)

def delete_document_cache(index: dict, title: str):
    slug = index.pop(title, None)
    if slug:
        p = _slug_path(slug)
        if p.exists():
            p.unlink()
    save_cache_index(index)

def migrate_legacy_cache_if_needed(index: dict, documents: dict) -> dict:
    if not LEGACY_CACHE_FILE.exists():
        return index
    legacy = load_json_safe(LEGACY_CACHE_FILE)
    if not isinstance(legacy, dict) or not legacy:
        return index
    CACHE_DIR.mkdir(exist_ok=True)
    migrated = 0
    for title, entry in legacy.items():
        url = documents.get(title) or entry.get("url", "")
        if not url:
            continue
        slug = index.get(title) or url_to_slug(url)
        index[title] = slug
        dest = _slug_path(slug)
        if not dest.exists():
            atomic_write_json(dest, entry)
            migrated += 1
    if migrated:
        save_cache_index(index)
        info(f"Migrated {migrated} entries from legacy status_cache.json")
    LEGACY_CACHE_FILE.rename(LEGACY_CACHE_FILE.with_suffix(".json.migrated"))
    return index

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

def make_related_document_record(
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
    source_section="",
    source_sections=None,
    source_toggle="",
    source_toggles=None,
    relation_type="",
    relation_types=None,
    depth=1,
):
    # Build the canonical related-document object.
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
        "source_section": source_section,
        "source_sections": source_sections or ([source_section] if source_section else []),
        "source_toggle": source_toggle,
        "source_toggles": source_toggles or ([source_toggle] if source_toggle else []),
        "relation_type": relation_type,
        "relation_types": relation_types or ([relation_type] if relation_type else []),
        "depth": depth,
    }

def normalize_cache_entry(entry):
    # Migrate older cache shapes into the current public response format.
    if not isinstance(entry, dict):
        entry = {}

    replacements = entry.get("replacements")
    if not isinstance(replacements, list):
        replacements = []
    related_documents = entry.get("related_documents")
    if not isinstance(related_documents, (list, dict)):
        related_documents = []

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

    normalized_related_documents = []
    for item in flatten_related_documents(related_documents):
        if not isinstance(item, dict):
            continue
        normalized_related_documents.append(
            make_related_document_record(
                normalized_status=item.get("normalized_status", ""),
                raw_status=item.get("raw_status", ""),
                so_hieu=item.get("so_hieu", ""),
                expired_date=item.get("expired_date"),
                last_checked=item.get("last_checked", ""),
                title=item.get("title", ""),
                url=item.get("url", ""),
                effective_date=item.get("effective_date", ""),
                error=item.get("error", ""),
                source_section=item.get("source_section", ""),
                source_sections=item.get("source_sections"),
                source_toggle=item.get("source_toggle", ""),
                source_toggles=item.get("source_toggles"),
                relation_type=item.get("relation_type", ""),
                relation_types=item.get("relation_types"),
                depth=item.get("depth", 1),
            )
        )

    normalized = {
        "normalized_status": normalized_status,
        "raw_status": raw_status,
        "so_hieu": so_hieu,
        "expired_date": expired_date,
        "replacements": normalized_replacements,
        "related_documents": group_related_documents_by_section(normalized_related_documents),
        "related_documents_collected": bool(entry.get("related_documents_collected")),
        "last_checked": last_checked,
    }

    if "error_type" in entry:
        normalized["error_type"] = entry.get("error_type")
    if "error_message" in entry:
        normalized["error_message"] = entry.get("error_message")
    if "attempts" in entry:
        normalized["attempts"] = entry.get("attempts")

    return normalized

def compact_json(value):
    # Render JSON on one line for txt output.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def build_empty_related_documents():
    # Keep response shape stable even when a document has no related links.
    grouped = {}
    for section in RELATED_DOCUMENT_SECTIONS:
        grouped.setdefault(section["section"], [])
    return grouped

def group_related_documents_by_section(items):
    # Expose related documents grouped by section in cache and API output.
    grouped = build_empty_related_documents()
    for item in items:
        if not isinstance(item, dict):
            continue
        section = item.get("source_section") or "Khác"
        grouped.setdefault(section, []).append(item)
    return grouped

def flatten_related_documents(grouped):
    # Internal iterator helper so the crawl flow can keep a simple sequential loop.
    if isinstance(grouped, list):
        return [item for item in grouped if isinstance(item, dict)]
    if not isinstance(grouped, dict):
        return []
    items = []
    for values in grouped.values():
        if isinstance(values, list):
            items.extend(item for item in values if isinstance(item, dict))
    return items

def count_related_documents(grouped):
    return len(flatten_related_documents(grouped))

def should_retry_cached_status(status: str) -> bool:
    # Retry cached documents only for unstable statuses.
    return status in RETRY_CACHE_STATUSES

def has_replacement_data(data: dict) -> bool:
    # Check whether an expired document already has replacement info.
    if data.get("normalized_status") != "EXPIRED":
        return True
    return bool(data.get("replacements"))

def has_related_document_data(data: dict) -> bool:
    # Empty related sections are valid once collection has completed.
    if not ENABLE_RELATED_DOCUMENTS:
        return True
    return bool(data.get("related_documents_collected"))

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

    try:
        await wait_for_luoc_do_ready(page)
    except PlaywrightTimeoutError:
        if await has_viewing_document_metadata(page):
            warn("Không load được tab Lược đồ nhưng metadata văn bản vẫn đọc được")
            return response, None
        warn("Không load được tab Lược đồ")
        return response, {
            "normalized_status": "NO_LUOCDO",
            "raw_status": None,
            "error_type": "NO_LUOCDO",
        }
    except Exception as exc:
        blocked_status, blocked_reason = await is_blocked_page(page)
        if blocked_status:
            level = "ERROR" if blocked_status == "BLOCKED" else "WARN"
            log(level, f"{blocked_status} detected: {blocked_reason}")
            return response, {
                "normalized_status": blocked_status,
                "raw_status": None,
                "error_type": blocked_reason,
                "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        if await has_viewing_document_metadata(page):
            warn(f"Không load được tab Lược đồ ({exc}) nhưng metadata văn bản vẫn đọc được")
            return response, None
        warn(f"Không load được tab Lược đồ: {exc}")
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

async def detect_invalid_account_state(page, metadata):
    # Fail fast when the page exposes the known non-Pro / bad-cookie pattern.
    effective_date = normalize(metadata.get("Ngày hiệu lực", ""))
    if effective_date != "Đã biết":
        return None

    replacement_header = await page.query_selector("#cmDiagram .ghd[onclick*='replaceDocument']")
    if replacement_header:
        header_text = normalize(await replacement_header.inner_text())
        if "Xem chi tiết" in header_text:
            return INVALID_ACCOUNT_ERROR_MESSAGE

    replacement_container = await page.query_selector("#replaceDocument")
    if replacement_container:
        container_text = normalize(await replacement_container.inner_text())
        if "Xem chi tiết" in container_text:
            return INVALID_ACCOUNT_ERROR_MESSAGE

    body_text = normalize(await page.locator("body").inner_text())
    if "Văn bản thay thế" in body_text and "Xem chi tiết" in body_text:
        return INVALID_ACCOUNT_ERROR_MESSAGE

    return None

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

    invalid_account_message = await detect_invalid_account_state(page, metadata)
    if invalid_account_message:
        return None, {
            "normalized_status": "INVALID_ACCOUNT",
            "raw_status": metadata.get("Tình trạng", ""),
            "so_hieu": metadata.get("Số hiệu", ""),
            "error_type": INVALID_ACCOUNT_ERROR_CODE,
            "error_message": invalid_account_message,
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    snapshot = build_snapshot_from_metadata(
        metadata,
        last_checked=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return snapshot, None

async def extract_replacement_documents(page, current_url):
    # Legacy wrapper: keep the old public behavior backed by the generic collector.
    replacement_sections = tuple(
        section for section in RELATED_DOCUMENT_SECTIONS if section["toggle_id"] == "replaceDocument"
    )
    replacements = await collect_related_documents(
        page,
        current_url,
        sections=replacement_sections,
        allowed_domains=ALLOWED_DOMAINS,
        deduplicate=DEDUP_RELATED_URLS,
        preserve_source_section=PRESERVE_SOURCE_SECTION,
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

def should_queue_related_retry(error_result):
    return error_result and error_result.get("normalized_status") == "BLOCKED"

def apply_related_snapshot(document, related_snapshot):
    document["normalized_status"] = related_snapshot["normalized_status"]
    document["raw_status"] = related_snapshot["raw_status"]
    document["so_hieu"] = related_snapshot["so_hieu"]
    document["expired_date"] = related_snapshot["expired_date"]
    document["effective_date"] = related_snapshot["effective_date"]
    document["last_checked"] = related_snapshot["last_checked"]
    document["error"] = ""

def apply_related_error(document, snapshot_error):
    error_type = snapshot_error.get("error_type") or "RELATED_DOCUMENT_SNAPSHOT_NOT_FOUND"
    document["normalized_status"] = snapshot_error.get("normalized_status", "")
    document["raw_status"] = snapshot_error.get("raw_status") or ""
    document["so_hieu"] = snapshot_error.get("so_hieu", "")
    document["expired_date"] = snapshot_error.get("expired_date")
    document["effective_date"] = ""
    document["error"] = error_type
    document["last_checked"] = snapshot_error.get("last_checked", document["last_checked"])
    return error_type

# ======================
# CHECK DOCUMENT
# ======================
async def check_document(page, title, url, *, max_related_urls=None, on_checkpoint=None):
    # Crawl one document, then enrich it with first-level related documents.
    info(f"Checking → {title}")

    result, open_error = await fetch_document_snapshot(page, url)
    if open_error:
        if open_error.get("error_type") == INVALID_ACCOUNT_ERROR_CODE:
            raise InvalidAccountStateError(open_error.get("error_message") or INVALID_ACCOUNT_ERROR_MESSAGE)
        return open_error

    result.pop("effective_date", None)
    info(f"Raw status: {result['raw_status']}")
    info(f"Normalized: {result['normalized_status']}")

    result["replacements"] = []
    result["related_documents"] = build_empty_related_documents()
    result["related_documents_collected"] = False

    if not ENABLE_RELATED_DOCUMENTS or MAX_DEEP < 1:
        return result

    related_docs = await collect_related_documents(
        page,
        url,
        sections=RELATED_DOCUMENT_SECTIONS,
        allowed_domains=ALLOWED_DOMAINS,
        deduplicate=DEDUP_RELATED_URLS,
        preserve_source_section=PRESERVE_SOURCE_SECTION,
        log_func=info if LOG_RELATED_DOCUMENT_MAPPING else None,
    )
    if max_related_urls is None:
        max_related_urls = MAX_RELATED_URLS_PER_DOCUMENT
    if max_related_urls is not None and max_related_urls >= 0:
        if len(related_docs) > max_related_urls:
            warn(f"Limit related URLs: {len(related_docs)} -> {max_related_urls}")
        related_docs = related_docs[:max_related_urls]

    related_documents = []
    for document in related_docs:
        record = make_related_document_record(
            title=document["title"],
            url=document["url"],
            source_section=document.get("source_section", ""),
            source_sections=document.get("source_sections", []),
            source_toggle=document.get("source_toggle", ""),
            source_toggles=document.get("source_toggles", []),
            relation_type=document.get("relation_type", ""),
            relation_types=document.get("relation_types", []),
            depth=document.get("depth", 1),
            last_checked=result["last_checked"],
        )
        # Giữ tooltip data để dùng thay thế navigation
        record["_tooltip_raw_status"] = document.get("tooltip_raw_status", "")
        record["_tooltip_so_hieu"] = document.get("tooltip_so_hieu", "")
        record["_tooltip_effective_date"] = document.get("tooltip_effective_date", "")
        related_documents.append(record)
    result["related_documents"] = group_related_documents_by_section(related_documents)
    result["related_documents_collected"] = True
    if on_checkpoint:
        on_checkpoint(result)

    replacements = [
        make_replacement_record(
            title=document["title"],
            url=document["url"],
            last_checked=document["last_checked"],
        )
        for document in related_documents
        if "replaceDocument" in document.get("source_toggles", [])
    ]

    related_retry_queue = []

    for document in related_documents:
        tooltip_raw = document.pop("_tooltip_raw_status", "")
        tooltip_so_hieu = document.pop("_tooltip_so_hieu", "")
        tooltip_effective_date = document.pop("_tooltip_effective_date", "")

        # Dùng tooltip status trực tiếp — không cần navigate sang related URL
        if tooltip_raw:
            normalized = classify_status(tooltip_raw)
            expired_date = extract_expired_date(tooltip_raw) if normalized == "EXPIRED" else None
            apply_related_snapshot(document, {
                "normalized_status": normalized,
                "raw_status": tooltip_raw,
                "so_hieu": tooltip_so_hieu,
                "expired_date": expired_date,
                "effective_date": tooltip_effective_date,
                "last_checked": result["last_checked"],
            })
            success(
                f"Related (tooltip) → {document['source_section']} | "
                f"{document['title']} | {normalized}"
            )
            document["attempts"] = 0
            if on_checkpoint:
                on_checkpoint(result)
            continue

        # Fallback: navigate nếu tooltip không có status
        info(
            f"Related → {document['source_section']} | "
            f"{document['title']} | {document['url']}"
        )
        related_snapshot, snapshot_error = await fetch_replacement_snapshot(
            page,
            document["url"],
        )
        document["attempts"] = 1

        if snapshot_error:
            error_type = apply_related_error(document, snapshot_error)
            warn(f"Không lấy được snapshot văn bản liên quan: {error_type}")
            if should_queue_related_retry(snapshot_error):
                related_retry_queue.append(document)
            if on_checkpoint:
                on_checkpoint(result)
            await sleep_random("Related-url delay", *RELATED_URL_SLEEP_RANGE)
            continue

        apply_related_snapshot(document, related_snapshot)
        success(
            f"Related snapshot: {related_snapshot['normalized_status']} | "
            f"{related_snapshot['effective_date'] or related_snapshot['expired_date'] or ''}"
        )
        if on_checkpoint:
            on_checkpoint(result)
        await sleep_random("Related-url delay", *RELATED_URL_SLEEP_RANGE)

    for retry_attempt in range(2, MAX_RETRIES_PER_RELATED_URL + 2):
        if not related_retry_queue:
            break

        retry_batch = related_retry_queue
        related_retry_queue = []
        warn(f"Retry queue văn bản liên quan: attempt {retry_attempt}/{MAX_RETRIES_PER_RELATED_URL + 1}, total={len(retry_batch)}")

        for document in retry_batch:
            await sleep_random("Related-retry queue delay", *RELATED_RETRY_SLEEP_RANGE)
            info(
                f"Related retry → {document['source_section']} | "
                f"{document['title']} | {document['url']}"
            )
            related_snapshot, snapshot_error = await fetch_replacement_snapshot(page, document["url"])
            document["attempts"] = retry_attempt

            if snapshot_error:
                error_type = apply_related_error(document, snapshot_error)
                warn(f"Retry vẫn chưa lấy được snapshot văn bản liên quan: {error_type}")
                if should_queue_related_retry(snapshot_error) and retry_attempt <= MAX_RETRIES_PER_RELATED_URL:
                    related_retry_queue.append(document)
                if on_checkpoint:
                    on_checkpoint(result)
                continue

            apply_related_snapshot(document, related_snapshot)
            success(
                f"Related retry snapshot: {related_snapshot['normalized_status']} | "
                f"{related_snapshot['effective_date'] or related_snapshot['expired_date'] or ''}"
            )
            if on_checkpoint:
                on_checkpoint(result)

    replacements_by_url = {item["url"].rstrip("/"): item for item in replacements}
    for document in related_documents:
        if "replaceDocument" not in document.get("source_toggles", []):
            continue
        replacement_item = replacements_by_url.get(document["url"].rstrip("/"))
        if not replacement_item:
            continue
        for field in (
            "normalized_status",
            "raw_status",
            "so_hieu",
            "expired_date",
            "effective_date",
            "last_checked",
            "error",
        ):
            replacement_item[field] = document.get(field, replacement_item.get(field, ""))
    result["replacements"] = replacements
    if on_checkpoint:
        on_checkpoint(result)

    return result

# Retry only transient failures; blocked pages are returned immediately.
# ======================
# RETRY WRAPPER
# ======================
async def check_document_with_retries(page, title, url, *, max_related_urls=None, on_checkpoint=None):
    # Retry unstable document checks before giving up.
    attempts = 0
    last_result = None

    while attempts <= MAX_RETRIES_PER_URL:
        attempts += 1
        info(f"Attempt {attempts}/{MAX_RETRIES_PER_URL + 1} → {title}")
        result = await check_document(
            page,
            title,
            url,
            max_related_urls=max_related_urls,
            on_checkpoint=on_checkpoint,
        )
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
def normalize_document_mapping(documents):
    # Normalize a title -> url mapping so CLI and API share the same rules.
    if not isinstance(documents, dict):
        raise ValueError("Documents input must be a mapping of title -> url")

    normalized = {}
    for raw_title, raw_url in documents.items():
        if raw_title is None or raw_url is None:
            raise ValueError("Document entries must not contain null title/url")

        title = str(raw_title).strip()
        url = str(raw_url).strip()
        if not title or not url:
            raise ValueError("Document entries must include non-empty title/url")
        if title in normalized:
            raise ValueError(f"Duplicate title after normalization: {title}")
        normalized[title] = url

    return normalized


async def open_verification_browser(url: str, context) -> bool:
    """Mở browser có giao diện để user giải captcha. Trả về True nếu giải thành công."""
    warn(f"[DEV] Mở browser để giải captcha thủ công: {url}")
    async with async_playwright() as p:
        headed = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1366,900",
            ],
        )
        headed_ctx = await headed.new_context(
            user_agent=USER_AGENT,
            viewport=BROWSER_VIEWPORT,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )

        # Copy cookies hiện tại sang browser mới
        existing_cookies = await context.cookies()
        if existing_cookies:
            await headed_ctx.add_cookies(existing_cookies)

        headed_page = await headed_ctx.new_page()
        try:
            await headed_page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

        warn("[DEV] Browser đã mở. Giải captcha rồi trang sẽ tự động tiếp tục (timeout 5 phút).")

        # Tự động phát hiện khi captcha được giải (poll mỗi 3 giây, tối đa 5 phút)
        solved = False
        for _ in range(100):
            await asyncio.sleep(3)
            try:
                blocked_status, _ = await is_blocked_page(headed_page)
                if not blocked_status:
                    solved = True
                    break
            except Exception:
                break

        if solved:
            info("[DEV] Captcha đã được giải thành công.")
        else:
            warn("[DEV] Hết thời gian chờ captcha (5 phút).")

        # Cập nhật cookies từ headed browser vào headless context
        new_cookies = await headed_ctx.cookies()
        if new_cookies:
            await context.add_cookies(new_cookies)
            info(f"[DEV] Đã cập nhật {len(new_cookies)} cookies vào session.")

        await headed.close()

    return solved


async def run_check_hieu_luc(documents, input_date):
    # Run the full crawl, then write summary files and cache.

    documents = normalize_document_mapping(documents)
    index = load_cache_index()
    index = migrate_legacy_cache_if_needed(index, documents)
    cache = load_per_document_cache(index, documents)
    rate_state = load_rate_limit_state()
    save_rate_limit_state(rate_state)

    # Chỉ giữ cache thuộc tập văn bản hiện tại để tránh số liệu bị nhiễu.
    stale_titles = [t for t in list(index.keys()) if t not in documents]
    if stale_titles:
        for t in stale_titles:
            delete_document_cache(index, t)
            cache.pop(t, None)
        info(f"Removed stale cache entries: {len(stale_titles)}")

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
        browser = await p.chromium.launch(
            headless=not DEV_MODE,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-plugins-discovery",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1366,900",
            ],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport=BROWSER_VIEWPORT,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )

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

                cache_ready = has_replacement_data(cache[title]) and has_related_document_data(cache[title])
                if not should_retry_cached_status(old_status) and cache_ready:
                    info(f"Skip (đã có trạng thái hợp lệ) → {title}")
                    skipped_cached.append((title, url, old_status))
                    continue
                elif old_status == "EXPIRED" and not has_replacement_data(cache[title]):
                    info(f"Rechecking expired document to fetch replacement → {title}")
                elif not has_related_document_data(cache[title]):
                    info(f"Rechecking document to fetch related URLs → {title}")
                else:
                    info(f"Rechecking cached failed status → {title} [{old_status}]")

            limit_reason = check_rate_limits(rate_state, urls_processed_this_run)
            if limit_reason:
                stop_reason = limit_reason
                warn(f"LIMIT_REACHED: {limit_reason}")
                break

            bytes_before = traffic["bytes_downloaded"]

            def checkpoint_result(partial_result):
                cache[title] = partial_result
                save_document_cache(index, title, url, partial_result)

            try:
                result = await check_document_with_retries(
                    page,
                    title,
                    url,
                    max_related_urls=MAX_RELATED_URLS_PER_DOCUMENT,
                    on_checkpoint=checkpoint_result,
                )
            except InvalidAccountStateError:
                error(INVALID_ACCOUNT_ERROR_MESSAGE)
                raise
            bytes_used = max(0, traffic["bytes_downloaded"] - bytes_before)

            cache[title] = result
            save_document_cache(index, title, url, result)

            urls_processed_this_run += 1
            rate_state["urls_processed_today"] += 1
            related_urls_processed = count_related_documents(result.get("related_documents", {}))
            urls_processed_this_run += related_urls_processed
            rate_state["urls_processed_today"] += related_urls_processed
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

            if urls_processed_this_run > MAX_URL_PER_RUN:
                warn(
                    f"Run quota exceeded while finishing current root: "
                    f"{urls_processed_this_run}/{MAX_URL_PER_RUN}"
                )
            if rate_state["urls_processed_today"] > MAX_URL_PER_DAY:
                warn(
                    f"Daily quota exceeded while finishing current root: "
                    f"{rate_state['urls_processed_today']}/{MAX_URL_PER_DAY}"
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
                "related_documents": data.get("related_documents", {}),
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
                f"replacements={row.get('replacements_text','')} | "
                f"related_documents={compact_json(row.get('related_documents', {}))}\n"
            )

    with open(expired_file, "w", encoding="utf-8") as f:
        for title, data in expired_before:
            f.write(
                f"{title} | {data.get('so_hieu','')} | "
                f"{data.get('raw_status','')} | "
                f"replacements={compact_json(data.get('replacements', []))} | "
                f"related_documents={compact_json(data.get('related_documents', {}))} \n"
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
    success(f"Cache saved → {CACHE_DIR}")

    return {
        "input_date": input_date.strftime("%d/%m/%Y"),
        "total_documents": len(documents),
        "urls_processed_this_run": urls_processed_this_run,
        "success_count": len(success_titles),
        "failed_count": len(failed_titles),
        "skipped_cache_count": len(skipped_cached),
        "blocked_reason": blocked_reason,
        "stop_reason": stop_reason,
        "all_results_file": str(all_results_file),
        "expired_file": str(expired_file),
        "stats": dict(stats),
        "all_results": all_results,
        "expired_before": [{"title": title, **data} for title, data in expired_before],
    }


async def main():
    # CLI wrapper: keep backward compatibility with the original invocation.

    if len(sys.argv) != 2:
        print("Usage: python check_hieu_luc.py dd/mm/YYYY")
        sys.exit(1)

    input_date = parse_date(sys.argv[1])
    if not input_date:
        print("Sai định dạng ngày")
        sys.exit(1)

    try:
        documents = normalize_document_mapping(load_json_safe(DATA_FILE))
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)

    try:
        await run_check_hieu_luc(documents, input_date)
    except InvalidAccountStateError as exc:
        print(exc.message)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
