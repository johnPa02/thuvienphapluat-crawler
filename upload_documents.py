#!/usr/bin/env python3
"""
Script to upload documents to the API based on CSV configuration.
"""

import csv
import os
import requests
import time
from pathlib import Path


# Configuration
API_URL = "https://bhyt-dev.lumination.vn/api/documents/import-from-txt"
DOCUMENTS_DIR = "/home/johny02/Documents/lumination/knowledge-graph/y-te"
CSV_FILE = "/home/johny02/projects/thuvienphapluat-crawler/documents.csv"


def upload_document(file_path: str, document_number: str, document_type: str, 
                    issuing_authority: str, title: str, issued_date: str) -> dict:
    """
    Upload a single document to the API.
    
    Args:
        file_path: Absolute path to the file
        document_number: Document number (e.g., "51/2024/QH15")
        document_type: Type of document (e.g., "Luật", "Thông tư")
        issuing_authority: Issuing authority (e.g., "Quốc hội", "Bộ Y tế")
        title: Document title
        issued_date: Issued date (format: DD/MM/YYYY)
    
    Returns:
        dict: API response
    """
    if not os.path.exists(file_path):
        return {"success": False, "error": f"File not found: {file_path}"}
    
    with open(file_path, 'rb') as f:
        files = {
            'file': (os.path.basename(file_path), f, 'text/plain')
        }
        data = {
            'document_number': document_number,
            'document_type': document_type,
            'issuing_authority': issuing_authority,
            'title': title,
            'issued_date': issued_date
        }
        
        try:
            response = requests.post(API_URL, files=files, data=data, timeout=120)
            response.raise_for_status()
            return {"success": True, "status_code": response.status_code, "response": response.json()}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": str(e)}


def main():
    """Main function to process CSV and upload documents."""
    
    # Track results
    results = {
        "success": [],
        "failed": [],
        "skipped": []
    }
    
    # Read CSV file
    with open(CSV_FILE, 'r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        
        for row in reader:
            filename = row.get('file', '').strip()
            
            # Skip empty rows
            if not filename:
                continue
            
            # Build full path
            file_path = os.path.join(DOCUMENTS_DIR, filename)
            
            # Check if file exists
            if not os.path.exists(file_path):
                print(f"⚠️  File not found, skipping: {filename}")
                results["skipped"].append({
                    "file": filename,
                    "reason": "File not found"
                })
                continue
            
            # Extract document info
            document_number = row.get('document_number', '').strip()
            document_type = row.get('document_type', '').strip()
            issuing_authority = row.get('issuing_authority', '').strip()
            title = row.get('title', '').strip()
            issued_date = row.get('issued_date', '').strip()
            
            print(f"\n📄 Uploading: {filename}")
            print(f"   Document Number: {document_number}")
            print(f"   Type: {document_type}")
            print(f"   Authority: {issuing_authority}")
            print(f"   Title: {title}")
            print(f"   Date: {issued_date}")
            
            # Upload document
            result = upload_document(
                file_path=file_path,
                document_number=document_number,
                document_type=document_type,
                issuing_authority=issuing_authority,
                title=title,
                issued_date=issued_date
            )
            
            if result.get("success"):
                print(f"   ✅ Success!")
                results["success"].append({
                    "file": filename,
                    "status_code": result.get("status_code")
                })
            else:
                print(f"   ❌ Failed: {result.get('error')}")
                results["failed"].append({
                    "file": filename,
                    "error": result.get("error")
                })
            
            # Small delay between requests to avoid overwhelming the server
            time.sleep(1)
    
    # Print summary
    print("\n" + "=" * 60)
    print("UPLOAD SUMMARY")
    print("=" * 60)
    print(f"✅ Success: {len(results['success'])} files")
    print(f"❌ Failed: {len(results['failed'])} files")
    print(f"⚠️  Skipped: {len(results['skipped'])} files")
    
    if results["failed"]:
        print("\nFailed uploads:")
        for item in results["failed"]:
            print(f"  - {item['file']}: {item['error']}")
    
    if results["skipped"]:
        print("\nSkipped files:")
        for item in results["skipped"]:
            print(f"  - {item['file']}: {item['reason']}")


if __name__ == "__main__":
    main()
