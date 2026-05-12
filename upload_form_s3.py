import os
import sys
import json
import csv
import re
import requests
import mimetypes

from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from dotenv import load_dotenv
import boto3


# ======================
# LOAD ENV
# ======================

load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = os.getenv("S3_PREFIX", "").strip("/")

required_vars = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "S3_BUCKET"
]

missing = [v for v in required_vars if not os.getenv(v)]

if missing:
    print(f"❌ Missing env vars: {', '.join(missing)}")
    sys.exit(1)


# ======================
# CONFIG
# ======================

INPUT_FOLDER = "all"
DOWNLOAD_FOLDER = "download_forms"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)


# ======================
# S3 CLIENT
# ======================

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
)


# ======================
# REGEX
# ======================

pattern = re.compile(
    r"\[([^\]]+)\]\((https?://[^)]+)\)",
    re.IGNORECASE
)


# ======================
# SANITIZE FILENAME
# ======================

def sanitize_filename(name):

    name = re.sub(r'[\\/:*?"<>|]', "-", name)
    name = name.strip()

    return name


# ======================
# GET FILENAME FROM URL
# ======================

def get_filename_from_url(url):

    parsed = urlparse(url)

    filename = os.path.basename(parsed.path)

    filename = unquote(filename)

    filename = sanitize_filename(filename)

    if not filename:
        filename = "file"

    return filename


# ======================
# DOWNLOAD FILE
# ======================

def download_file(url, save_path):

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*"
    }

    try:

        r = requests.get(url, headers=headers, timeout=60, stream=True)

        if r.status_code == 200:

            with open(save_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)

            return True

        else:
            print(f"❌ HTTP {r.status_code}: {url}")

    except Exception as e:
        print("❌ Download error:", e)

    return False


# ======================
# UPLOAD FILE TO S3
# ======================

def upload_file(file_path: Path):

    s3_key = f"{S3_PREFIX}/{file_path.name}" if S3_PREFIX else file_path.name

    try:

        content_type, _ = mimetypes.guess_type(file_path)

        extra_args = {}

        if content_type:
            extra_args["ContentType"] = content_type

        s3.upload_file(
            str(file_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs=extra_args if extra_args else None
        )

        encoded_key = quote(s3_key, safe="/")

        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{encoded_key}"

        return {"success": True, "s3": url}

    except Exception as e:

        return {"success": False, "error": str(e)}


# ======================
# MAIN
# ======================

results = []
errors = []
json_mapping = []

seen_urls = set()

stats = {
    "total_txt": 0,
    "total_forms": 0,
    "download_success": 0,
    "download_failed": 0,
    "upload_success": 0,
    "upload_failed": 0,
    "local_exists": 0
}

txt_files = list(Path(INPUT_FOLDER).glob("*.txt"))

stats["total_txt"] = len(txt_files)

print(f"📄 Found {len(txt_files)} txt files")


for txt_file in txt_files:

    raw_doc_name = txt_file.stem
    document_name = raw_doc_name.replace("_", "/")

    print(f"\nProcessing: {document_name}")

    with open(txt_file, "r", encoding="utf-8") as f:
        content = f.read()

    matches = pattern.findall(content)

    stats["total_forms"] += len(matches)

    print(f"Found {len(matches)} forms")


    for form_name, url in matches:

        print(" →", form_name)

        filename = get_filename_from_url(url)

        local_path = Path(DOWNLOAD_FOLDER) / filename


        # ======================
        # DOWNLOAD
        # ======================

        if local_path.exists():

            stats["local_exists"] += 1

        else:

            ok = download_file(url, local_path)

            if not ok:

                stats["download_failed"] += 1

                errors.append({
                    "type": "download_failed",
                    "url": url,
                    "file": filename
                })

                continue

            stats["download_success"] += 1


        # ======================
        # UPLOAD S3
        # ======================

        upload_result = upload_file(local_path)

        if not upload_result["success"]:

            stats["upload_failed"] += 1

            errors.append({
                "type": "upload_failed",
                "url": url,
                "file": filename,
                "error": upload_result["error"]
            })

            print("❌ Upload failed:", upload_result["error"])

            continue

        stats["upload_success"] += 1

        s3_url = upload_result["s3"]


        # ======================
        # SAVE CSV
        # ======================

        results.append({
            "ten_van_ban": document_name,
            "ten_mau": form_name,
            "original_url": url,
            "s3_url": s3_url
        })


        # ======================
        # SAVE JSON MAP
        # ======================

        if url not in seen_urls:

            json_mapping.append({
                "old": url,
                "new": s3_url
            })

            seen_urls.add(url)


# ======================
# EXPORT CSV
# ======================

with open("mapping_forms.csv", "w", newline="", encoding="utf-8") as f:

    writer = csv.DictWriter(
        f,
        fieldnames=["ten_van_ban", "ten_mau", "original_url", "s3_url"]
    )

    writer.writeheader()
    writer.writerows(results)


# ======================
# EXPORT JSON
# ======================

with open("mapping_forms.json", "w", encoding="utf-8") as f:

    json.dump(json_mapping, f, ensure_ascii=False, indent=2)


# ======================
# EXPORT ERROR LOG
# ======================

if errors:

    with open("errors_forms.csv", "w", newline="", encoding="utf-8") as f:

        writer = csv.DictWriter(
            f,
            fieldnames=["type", "url", "file", "error"]
        )

        writer.writeheader()
        writer.writerows(errors)


# ======================
# SUMMARY
# ======================

print("\n" + "="*60)
print("SUMMARY")
print("="*60)

print(f"TXT files scanned        : {stats['total_txt']}")
print(f"Forms found              : {stats['total_forms']}")

print(f"Local file exists        : {stats['local_exists']}")

print(f"Download success         : {stats['download_success']}")
print(f"Download failed          : {stats['download_failed']}")

print(f"Upload success           : {stats['upload_success']}")
print(f"Upload failed            : {stats['upload_failed']}")

print(f"JSON mappings created    : {len(json_mapping)}")

print("="*60)

print("✅ mapping_forms.json created")

if errors:
    print("⚠️ errors_forms.csv created")