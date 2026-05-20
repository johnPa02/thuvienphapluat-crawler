from pathlib import Path
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
            "data": raw["all_results"],
            "input_date": raw["input_date"],
            "total_documents": raw["total_documents"],
            "urls_processed_this_run": raw["urls_processed_this_run"],
            "success_count": raw["success_count"],
            "failed_count": raw["failed_count"],
            "skipped_cache_count": raw["skipped_cache_count"],
            "blocked_reason": raw["blocked_reason"],
            "stop_reason": raw["stop_reason"],
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
