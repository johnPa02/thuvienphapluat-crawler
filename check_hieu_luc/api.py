from pathlib import Path
import json
import sys

if __package__ in {None, ""}:
    # Allow running as `python check_hieu_luc/api.py` from the repo root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from urllib.parse import urlparse

try:
    from .check_hieu_luc import (
        InvalidAccountStateError,
        classify_document_age,
        parse_cookies_from_text,
        parse_date,
        run_check_hieu_luc,
    )
except ImportError:
    from check_hieu_luc.check_hieu_luc import (
        InvalidAccountStateError,
        classify_document_age,
        parse_cookies_from_text,
        parse_date,
        run_check_hieu_luc,
    )

_REPO_ROOT = Path(__file__).resolve().parent.parent
_S3_MAPPING_FILE = _REPO_ROOT / "law_list_with_s3.json"


def _load_s3_mapping() -> dict[str, str | None]:
    """Build origin_url -> s3_url lookup from law_list_with_s3.json.

    Includes all entries with a thuvienphapluat.vn origin URL regardless of
    whether an S3 link has been uploaded yet (s3 may be null).
    """
    try:
        entries = json.loads(_S3_MAPPING_FILE.read_text(encoding="utf-8"))
        result = {}
        for e in entries:
            origin = (e.get("origin") or "").rstrip("/")
            if origin and "thuvienphapluat.vn" in origin:
                result[origin] = e.get("s3") or None
        return result
    except Exception:
        return {}


_S3_MAP: dict[str, str] = _load_s3_mapping()


def _normalize_url(url: str) -> str:
    return url.rstrip("/")


def _filter_and_enrich_related_documents(related_documents: dict, input_date) -> dict:
    """Filter related docs to origin-mapped only, then add document_age to each item."""
    filtered = {}
    for section_name, docs in related_documents.items():
        enriched = []
        for doc in docs:
            if _normalize_url(doc.get("url", "")) not in _S3_MAP:
                continue
            item = dict(doc)
            item["document_age"] = classify_document_age(item.get("effective_date", ""), input_date)
            enriched.append(item)
        filtered[section_name] = enriched
    return filtered


def _build_response_data(all_results: list, input_date) -> list[dict]:
    """Filter related_documents by S3 origin map and enrich with document_age."""
    output = []
    for result in all_results:
        item = dict(result)
        if item.get("related_documents"):
            item["related_documents"] = _filter_and_enrich_related_documents(
                item["related_documents"], input_date
            )
        # Enrich replacements with document_age too
        if item.get("replacements"):
            item["replacements"] = [
                {**r, "document_age": classify_document_age(r.get("effective_date", ""), input_date)}
                for r in item["replacements"]
            ]
        output.append(item)
    return output


app = FastAPI(title="check_hieu_luc API", version="1.0.0")


def is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/check-hieu-luc")
async def check_hieu_luc(
    input_date: str = Form(...),
    documents: str = Form(...),
    cookie_file: UploadFile = File(...),
):
    # Validate and parse cookie file
    cookie_bytes = await cookie_file.read()
    if not cookie_bytes:
        raise HTTPException(status_code=400, detail="Cookie file is empty or invalid")
    parsed_cookies = parse_cookies_from_text(cookie_bytes.decode("utf-8", errors="replace"))
    if not parsed_cookies:
        raise HTTPException(status_code=400, detail="Cookie file is empty or invalid")

    # Validate date
    parsed_date = parse_date(input_date)
    if not parsed_date:
        raise HTTPException(status_code=400, detail="Sai định dạng ngày, dùng dd/mm/YYYY")

    # Normalize documents string: strip outer quotes and collapse newlines injected by Postman
    documents = documents.strip()
    if documents.startswith('"') and documents.endswith('"'):
        documents = documents[1:-1]
    documents = " ".join(documents.split())
    try:
        doc_list = json.loads(documents)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="documents must be valid JSON")
    if not isinstance(doc_list, list) or not doc_list:
        raise HTTPException(status_code=400, detail="documents must be a non-empty list")

    docs = {}
    for item in doc_list:
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title or not url:
            raise HTTPException(status_code=400, detail="Each document must include non-empty title and url")
        if not is_valid_http_url(url):
            raise HTTPException(status_code=400, detail=f"Invalid URL: {url}")
        if title in docs:
            raise HTTPException(status_code=400, detail=f"Duplicate title after normalization: {title}")
        docs[title] = url

    try:
        raw = await run_check_hieu_luc(docs, parsed_date, cookies=parsed_cookies)
        return {
            "status": "success",
            "message": f"Đã kiểm tra {raw['total_documents']} văn bản",
            "input_date": raw["input_date"],
            "total_documents": raw["total_documents"],
            "urls_processed_this_run": raw["urls_processed_this_run"],
            "success_count": raw["success_count"],
            "failed_count": raw["failed_count"],
            "skipped_cache_count": raw["skipped_cache_count"],
            "blocked_reason": raw["blocked_reason"],
            "stop_reason": raw["stop_reason"],
            "data": _build_response_data(raw["all_results"], parsed_date),
        }
    except InvalidAccountStateError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
            },
        ) from exc


def serve():
    import uvicorn

    uvicorn.run("check_hieu_luc.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    serve()
