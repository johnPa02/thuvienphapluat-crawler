#!/usr/bin/env python3

import os
import sys
import boto3
import mimetypes
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
# UPLOAD IMAGE FUNCTION
# ======================

def upload_image(file_path: Path):

    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        sys.exit(1)

    # Detect MIME type
    content_type, _ = mimetypes.guess_type(file_path.name)

    if not content_type or not content_type.startswith("image/"):
        print(f"❌ File is not a valid image: {file_path}")
        sys.exit(1)

    s3 = get_s3_client()

    s3_key = f"{S3_PREFIX}/{file_path.name}" if S3_PREFIX else file_path.name

    try:
        s3.upload_file(
            str(file_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": content_type},
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
        print("  python upload_image_s3.py <image.png/jpg>")
        sys.exit(1)

    file_path = Path(sys.argv[1])

    upload_image(file_path)

if __name__ == "__main__":
    main()