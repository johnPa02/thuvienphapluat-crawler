#!/usr/bin/env python3

import os
import sys
import boto3
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


# ======================
# UPLOAD FUNCTION
# ======================

def upload_pdf(file_path: Path):

    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        sys.exit(1)

    s3 = get_s3_client()

    s3_key = f"{S3_PREFIX}/{file_path.name}" if S3_PREFIX else file_path.name

    try:
        s3.upload_file(
            str(file_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": "application/pdf"},
        )

        encoded_key = quote(s3_key, safe="/")

        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{encoded_key}"

        print("\n✅ Upload success")
        print(f"S3 URL:\n{url}")

        return url

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        sys.exit(1)


# ======================
# MAIN
# ======================

def main():

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python upload_one_pdf_s3.py <file.pdf>")
        sys.exit(1)

    file_path = Path(sys.argv[1])

    upload_pdf(file_path)


if __name__ == "__main__":
    main()