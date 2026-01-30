#!/usr/bin/env python3
"""
Full OCR for PDF using Qwen3-VL-8B.
Outputs text file with same name as PDF.
Supports resume from last processed page.
"""

import argparse
import base64
import io
import json
import os
import time
from datetime import datetime, timedelta

import fitz  # PyMuPDF
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()

# Qwen3 VL 8B on HuggingFace - Load from .env
CLIENT = OpenAI(
    base_url=os.getenv('QWEN_BASE_URL', 'https://jd5nnmh2rciko6ts.us-east-1.aws.endpoints.huggingface.cloud/v1/'),
    api_key=os.getenv('QWEN_API_KEY', os.getenv('HF_API_KEY', ''))
)
MODEL = os.getenv('QWEN_MODEL', 'unsloth/Qwen3-VL-8B-Instruct-GGUF')

OCR_PROMPT = """Trích xuất toàn bộ text từ hình ảnh này. 
Đây là văn bản pháp luật Việt Nam, có thể chứa bảng.
Nếu có bảng, hãy format thành markdown table với đầy đủ các cột.
Giữ nguyên định dạng và thứ tự của văn bản.
Không thêm giải thích, chỉ trả về nội dung được trích xuất."""


def pdf_page_to_image(doc, page_num: int, dpi: int = 150) -> bytes:
    """Convert a single PDF page to PNG image bytes."""
    page = doc.load_page(page_num)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


def ocr_image(image_bytes: bytes, max_retries: int = 3) -> str:
    """OCR image using Qwen3-VL-8B with retry logic."""
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    for attempt in range(max_retries):
        try:
            response = CLIENT.chat.completions.create(
                model=MODEL,
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{base64_image}'}},
                        {'type': 'text', 'text': OCR_PROMPT}
                    ]
                }],
                max_tokens=4096,
                timeout=120
            )
            return response.choices[0].message.content
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"    ⚠️ Retry {attempt + 1}/{max_retries}: {e}")
                time.sleep(5)
            else:
                print(f"    ❌ Failed after {max_retries} attempts: {e}")
                return f"[OCR ERROR: {e}]"
    return ""


def load_progress(progress_file: str) -> dict:
    """Load progress from file."""
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            return json.load(f)
    return {"last_page": -1, "pages_done": []}


def save_progress(progress_file: str, progress: dict):
    """Save progress to file."""
    with open(progress_file, 'w') as f:
        json.dump(progress, f)


def format_time(seconds: float) -> str:
    """Format seconds to human readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


def ocr_pdf(pdf_path: str, output_path: str = None, start_page: int = 0, end_page: int = None):
    """
    OCR entire PDF and save to text file.
    
    Args:
        pdf_path: Path to PDF file
        output_path: Output text file path (default: same as PDF with .txt extension)
        start_page: Start page (0-indexed, default: 0)
        end_page: End page (exclusive, default: all pages)
    """
    if not os.path.exists(pdf_path):
        print(f"❌ PDF not found: {pdf_path}")
        return
    
    # Determine output path
    if output_path is None:
        output_path = os.path.splitext(pdf_path)[0] + ".txt"
    
    progress_file = output_path + ".progress.json"
    
    print(f"📄 PDF: {pdf_path}")
    print(f"📝 Output: {output_path}")
    print(f"🤖 Model: {MODEL}")
    print("=" * 60)
    
    # Open PDF
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    if end_page is None:
        end_page = total_pages
    
    # Load progress
    progress = load_progress(progress_file)
    resume_page = max(progress["last_page"] + 1, start_page)
    
    if resume_page > start_page:
        print(f"📌 Resuming from page {resume_page + 1}")
    
    # Open output file in append mode
    mode = 'a' if resume_page > start_page else 'w'
    
    pages_to_process = end_page - resume_page
    times = []
    
    print(f"📊 Processing pages {resume_page + 1} to {end_page} ({pages_to_process} pages)")
    print("=" * 60)
    
    start_time = time.time()
    
    with open(output_path, mode, encoding='utf-8') as f:
        if mode == 'w':
            f.write(f"# OCR Output: {os.path.basename(pdf_path)}\n")
            f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Model: {MODEL}\n")
            f.write("=" * 60 + "\n\n")
        
        for page_num in range(resume_page, end_page):
            page_start = time.time()
            
            # Convert page to image
            image_bytes = pdf_page_to_image(doc, page_num)
            
            # OCR
            text = ocr_image(image_bytes)
            
            # Write to file
            f.write(f"\n{'='*60}\n")
            f.write(f"# PAGE {page_num + 1}\n")
            f.write(f"{'='*60}\n\n")
            f.write(text)
            f.write("\n")
            f.flush()
            
            # Track time
            page_time = time.time() - page_start
            times.append(page_time)
            
            # Update progress
            progress["last_page"] = page_num
            progress["pages_done"].append(page_num)
            save_progress(progress_file, progress)
            
            # Calculate ETA
            avg_time = sum(times) / len(times)
            remaining_pages = end_page - page_num - 1
            eta_seconds = avg_time * remaining_pages
            
            # Print progress
            elapsed = time.time() - start_time
            print(f"✅ Page {page_num + 1}/{end_page} | "
                  f"Time: {page_time:.1f}s | "
                  f"Avg: {avg_time:.1f}s | "
                  f"Elapsed: {format_time(elapsed)} | "
                  f"ETA: {format_time(eta_seconds)}")
    
    doc.close()
    
    # Cleanup progress file on completion
    if resume_page == start_page and progress["last_page"] == end_page - 1:
        os.remove(progress_file)
    
    total_time = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"✅ OCR Complete!")
    print(f"📊 Pages processed: {len(times)}")
    print(f"⏱️  Total time: {format_time(total_time)}")
    print(f"📝 Output saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="OCR PDF using Qwen3-VL-8B")
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("-o", "--output", help="Output text file path")
    parser.add_argument("-s", "--start", type=int, default=0, help="Start page (0-indexed)")
    parser.add_argument("-e", "--end", type=int, help="End page (exclusive)")
    
    args = parser.parse_args()
    
    ocr_pdf(args.pdf_path, args.output, args.start, args.end)


if __name__ == "__main__":
    main()
