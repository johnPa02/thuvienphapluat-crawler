from pathlib import Path
import json
import sys

if __package__ in {None, ""}:
    # Allow running as `python check_hieu_luc/api.py` from the repo root.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from urllib.parse import urlparse

try:
    from .check_hieu_luc import (
        InvalidAccountStateError,
        parse_date,
        run_check_hieu_luc,
    )
except ImportError:
    from check_hieu_luc.check_hieu_luc import (
        InvalidAccountStateError,
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


def _filter_related_documents(related_documents: dict) -> dict:
    """Keep original related_documents structure but only include entries whose url is in S3 mapping."""
    filtered = {}
    for section_name, docs in related_documents.items():
        filtered[section_name] = [
            doc for doc in docs
            if _normalize_url(doc.get("url", "")) in _S3_MAP
        ]
    return filtered


def _build_response_data(all_results: list) -> list[dict]:
    """Keep original result structure, only filter related_documents items to mapped-only."""
    output = []
    for result in all_results:
        item = dict(result)
        if "related_documents" in item and item["related_documents"]:
            item["related_documents"] = _filter_related_documents(item["related_documents"])
        output.append(item)
    return output


class DocumentItem(BaseModel):
    title: str
    url: str


class CheckHieuLucRequest(BaseModel):
    input_date: str
    documents: list[DocumentItem]


app = FastAPI(title="check_hieu_luc API", version="1.0.0")


def is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/check-hieu-luc")
async def check_hieu_luc(payload: CheckHieuLucRequest):
    input_date = parse_date(payload.input_date)
    if not input_date:
        raise HTTPException(status_code=400, detail="Sai định dạng ngày, dùng dd/mm/YYYY")

    if not payload.documents:
        raise HTTPException(status_code=400, detail="documents must be a non-empty list")

    documents = {}
    for item in payload.documents:
        title = str(item.title).strip()
        url = str(item.url).strip()
        if not title or not url:
            raise HTTPException(status_code=400, detail="Each document must include non-empty title and url")
        if not is_valid_http_url(url):
            raise HTTPException(status_code=400, detail=f"Invalid URL: {url}")
        if title in documents:
            raise HTTPException(status_code=400, detail=f"Duplicate title after normalization: {title}")
        documents[title] = url

    try:
        raw = await run_check_hieu_luc(documents, input_date)
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
            "data": _build_response_data(raw["all_results"]),
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
