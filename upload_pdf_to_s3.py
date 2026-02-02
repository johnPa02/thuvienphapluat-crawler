#!/usr/bin/env python3
"""
Script to upload PDF files from local directory to S3.
Returns permanent public URLs for uploaded files.
"""

import os
import sys
import boto3
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# S3 Configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = os.getenv("S3_PREFIX", "")  # optional folder prefix in S3

# Validate required environment variables
required_vars = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_BUCKET"]
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
    print("   Please set them in .env file")
    sys.exit(1)


def get_s3_client():
    """Create and return an S3 client."""
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )


def upload_file_to_s3(file_path: str, s3_key: str = None) -> dict:
    """
    Upload a single file to S3.
    
    Args:
        file_path: Local path to the file
        s3_key: Optional custom S3 key. If not provided, uses the filename with prefix.
    
    Returns:
        dict with success status and URL or error message
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    
    if not file_path.suffix.lower() == ".pdf":
        return {"success": False, "error": f"Not a PDF file: {file_path}"}
    
    # Generate S3 key if not provided
    if s3_key is None:
        s3_key = f"{S3_PREFIX}/{file_path.name}" if S3_PREFIX else file_path.name
    
    try:
        s3_client = get_s3_client()
        
        # Upload with content type
        s3_client.upload_file(
            str(file_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": "application/pdf"}
        )
        
        # Generate permanent public URL
        # URL encode the key for special characters
        encoded_key = quote(s3_key, safe="/")
        public_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{encoded_key}"
        
        return {
            "success": True,
            "s3_key": s3_key,
            "url": public_url,
            "file_name": file_path.name
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def upload_directory(directory_path: str, recursive: bool = False) -> list:
    """
    Upload all PDF files from a directory to S3.
    
    Args:
        directory_path: Path to the directory containing PDF files
        recursive: If True, also upload PDFs from subdirectories
    
    Returns:
        List of upload results
    """
    directory = Path(directory_path)
    
    if not directory.exists():
        print(f"❌ Directory not found: {directory}")
        return []
    
    if not directory.is_dir():
        print(f"❌ Not a directory: {directory}")
        return []
    
    # Find all PDF files
    if recursive:
        pdf_files = list(directory.rglob("*.pdf"))
    else:
        pdf_files = list(directory.glob("*.pdf"))
    
    if not pdf_files:
        print(f"⚠️  No PDF files found in: {directory}")
        return []
    
    print(f"📁 Found {len(pdf_files)} PDF file(s) in {directory}")
    print("-" * 60)
    
    results = []
    for pdf_file in pdf_files:
        print(f"\n📄 Uploading: {pdf_file.name}")
        result = upload_file_to_s3(str(pdf_file))
        results.append(result)
        
        if result["success"]:
            print(f"   ✅ Success!")
            print(f"   🔗 URL: {result['url']}")
        else:
            print(f"   ❌ Failed: {result['error']}")
    
    return results


def main():
    """Main function to upload PDF files to S3."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Upload single file:    python upload_pdf_to_s3.py <file.pdf>")
        print("  Upload directory:      python upload_pdf_to_s3.py <directory>")
        print("  Upload dir recursive:  python upload_pdf_to_s3.py <directory> --recursive")
        print()
        print("Examples:")
        print("  python upload_pdf_to_s3.py ocr/data/document.pdf")
        print("  python upload_pdf_to_s3.py ocr/data/")
        print("  python upload_pdf_to_s3.py ocr/data/ --recursive")
        sys.exit(1)
    
    path = sys.argv[1]
    recursive = "--recursive" in sys.argv or "-r" in sys.argv
    
    path_obj = Path(path)
    
    if path_obj.is_file():
        # Upload single file
        print(f"📄 Uploading file: {path}")
        result = upload_file_to_s3(path)
        
        if result["success"]:
            print(f"✅ Upload successful!")
            print(f"🔗 Permanent URL: {result['url']}")
        else:
            print(f"❌ Upload failed: {result['error']}")
            sys.exit(1)
    
    elif path_obj.is_dir():
        # Upload directory
        results = upload_directory(path, recursive=recursive)
        
        # Print summary
        success_count = sum(1 for r in results if r["success"])
        failed_count = sum(1 for r in results if not r["success"])
        
        print("\n" + "=" * 60)
        print("UPLOAD SUMMARY")
        print("=" * 60)
        print(f"✅ Success: {success_count} file(s)")
        print(f"❌ Failed: {failed_count} file(s)")
        
        if success_count > 0:
            print("\n📋 Permanent URLs:")
            for r in results:
                if r["success"]:
                    print(f"   {r['file_name']}")
                    print(f"   → {r['url']}")
                    print()
    
    else:
        print(f"❌ Path not found: {path}")
        sys.exit(1)


"""# Upload 1 file
uv run python upload_pdf_to_s3.py ocr/data/document.pdf

# Upload cả thư mục
uv run python upload_pdf_to_s3.py ocr/data/

# Upload recursive
uv run python upload_pdf_to_s3.py ocr/data/ --recursive"""

if __name__ == "__main__":
    main()
