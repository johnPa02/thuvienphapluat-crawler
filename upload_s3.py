#!/usr/bin/env python3
"""
Upload PDF files to S3 and export mapping chuẩn theo data.json
"""

import os
import sys
import json
import boto3
import re
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

# ======================
# LOAD ENV
# ======================
load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = os.getenv("S3_PREFIX", "").strip("/")

OUTPUT_JSON = "s3.json"
DATA_JSON = "data_all_law.json"

required_vars = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_BUCKET"]
missing = [v for v in required_vars if not os.getenv(v)]
if missing:
    print(f"❌ Missing env vars: {', '.join(missing)}")
    sys.exit(1)


# ======================
# NORMALIZE
# ======================
def normalize_name(name: str) -> str:
    name = name.lower()
    name = name.replace("/", "_")
    name = re.sub(r"\s+", " ", name)
    name = name.strip()
    return name


# ======================
# S3 CLIENT
# ======================
def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )


def upload_pdf(file_path: Path) -> dict:
    s3_key = f"{S3_PREFIX}/{file_path.name}" if S3_PREFIX else file_path.name

    try:
        s3 = get_s3_client()
        s3.upload_file(
            str(file_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": "application/pdf"},
        )

        encoded_key = quote(s3_key, safe="/")
        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{encoded_key}"

        return {"success": True, "s3": url}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ======================
# MAIN LOGIC
# ======================
def upload_directory_and_export_json(directory: Path, recursive=False):

    if not Path(DATA_JSON).exists():
        print(f"❌ Missing {DATA_JSON}")
        sys.exit(1)

    # Load data.json
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        origin_data = json.load(f)

    # Build normalized lookup
    normalized_lookup = {
        normalize_name(k): (k, v)
        for k, v in origin_data.items()
    }

    # Find PDFs
    pdfs = list(directory.rglob("*.pdf")) if recursive else list(directory.glob("*.pdf"))

    if not pdfs:
        print("⚠️ No PDF files found")
        return

    print(f"📁 Found {len(pdfs)} PDF files")
    print("-" * 60)

    results = []
    success = 0
    failed = 0

    for pdf in pdfs:
        print(f"📄 Uploading: {pdf.name}")

        normalized_file = normalize_name(pdf.stem)

        if normalized_file not in normalized_lookup:
            print("   ❌ No matching entry in data.json")
            failed += 1
            continue

        original_name, origin_link = normalized_lookup[normalized_file]

        upload_result = upload_pdf(pdf)

        if upload_result["success"]:
            success += 1

            results.append({
                "name": original_name,   # ✅ dùng tên chuẩn từ data.json
                "s3": upload_result["s3"],
                "origin": origin_link
            })

            print("   ✅ OK")

        else:
            failed += 1
            print(f"   ❌ FAILED: {upload_result['error']}")

    # Save JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    print("\n" + "=" * 60)
    print("UPLOAD SUMMARY")
    print("=" * 60)
    print(f"✅ Success: {success}")
    print(f"❌ Failed: {failed}")
    print(f"📄 JSON saved: {OUTPUT_JSON}")


# ======================
# ENTRY
# ======================
def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python upload_pdf_to_s3.py <documents_pdf>")
        print("  python upload_pdf_to_s3.py <documents_pdf> --recursive")
        sys.exit(1)

    path = Path(sys.argv[1])
    recursive = "--recursive" in sys.argv or "-r" in sys.argv

    if not path.exists() or not path.is_dir():
        print(f"❌ Directory not found: {path}")
        sys.exit(1)

    upload_directory_and_export_json(path, recursive)


if __name__ == "__main__":
    main()