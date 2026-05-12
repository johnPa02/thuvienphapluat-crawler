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
    base_url=os.getenv('QWEN_BASE_URL', 'http://42.96.46.234:8080/v1'),
    api_key=os.getenv('QWEN_API_KEY', '')  # nhiều server local không cần key
)

MODEL = os.getenv('QWEN_MODEL', 'Qwen3-VL-8B')

OCR_PROMPT = """
Bạn đang thực hiện OCR cho tài liệu brochure / infographic du lịch có layout phức tạp (text không theo thứ tự trên xuống).
MỤC TIÊU:
Trích xuất nội dung thông tin chính xác + hiểu đúng cấu trúc logic của trang, KHÔNG phụ thuộc vị trí text.
QUY TẮC QUAN TRỌNG:
1. NHẬN DIỆN TIÊU ĐỀ (RẤT QUAN TRỌNG):
- Tiêu đề có thể nằm ở bất kỳ vị trí nào (không chỉ đầu trang)
- Nhận diện tiêu đề dựa vào:
  + chữ IN HOA
  + kích thước lớn
  + nổi bật về mặt thị giác
- Ví dụ: "04 NATIONAL SPECIAL HISTORICAL SITES" là TIÊU ĐỀ CHÍNH
→ Luôn đưa tiêu đề chính lên đầu output:
# <TITLE>
2. LIÊN KẾT TEXT VỚI HÌNH ẢNH:
- Caption gần hình ảnh là thông tin hợp lệ
- Ví dụ:
  "Independence Palace Historical Site"
  "Cu Chi Tunnels Historical Site"
→ coi là danh sách địa điểm
3. BỎ QUA:
- text trang trí (background text, watermark)
- chữ bị lặp lại không mang thông tin
- text nằm trong ảnh không liên quan nội dung chính
- Tuy nhiên nếu text nằm trong hình ảnh nhưng có liên quan tới thông tin cần lấy thì cần lấy thông tin text đó 
4. TỔ CHỨC OUTPUT:
- Nếu có tiêu đề chính:
  # TITLE
- Nhóm các nội dung liên quan thành danh sách, ví dụ:
## Historical Sites
- Independence Palace Historical Site
- Cu Chi Tunnels Historical Site
- Ho Chi Minh Sea Trail Historical Site – Loc An Wharf (Xuyen Moc)
- Con Dao Prison Historical Site
5. KHÔNG phụ thuộc thứ tự đọc:
→ ưu tiên cấu trúc logic hơn vị trí trên ảnh
6. KHÔNG diễn giải, KHÔNG thêm nội dung
OUTPUT:
Plain text, format markdown nhẹ, rõ cấu trúc
KHÔNG giải thích
"""


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
    """OCR image using Qwen3-VL-8B with improved retry logic for 503 Loading model."""
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    attempt = 0
    consecutive_503 = 0
    max_503_waits = 5  # Số lần tối đa chờ model load (mỗi lần tăng thời gian)
    
    while attempt < max_retries or consecutive_503 < max_503_waits:
        try:
            # Tăng timeout nếu đang chờ model load
            timeout = 300 if consecutive_503 > 0 else 120
            
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
                timeout=timeout
            )
            return response.choices[0].message.content
            
        except Exception as e:
            error_msg = str(e)
            
            # Xử lý lỗi 503 "Loading model"
            if "503" in error_msg and "Loading model" in error_msg:
                consecutive_503 += 1
                if consecutive_503 <= max_503_waits:
                    # Exponential backoff: 10s, 20s, 40s, 60s, 60s
                    wait_time = min(10 * (2 ** (consecutive_503 - 1)), 60)
                    print(f"    ⏳ Model đang load (lần {consecutive_503}/{max_503_waits}), chờ {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"    ❌ Model không load sau {max_503_waits} lần chờ")
                    return f"[OCR ERROR: Model unavailable after loading timeout]"
            
            # Xử lý các lỗi khác với retry thông thường
            attempt += 1
            if attempt < max_retries:
                wait_time = 5 * (2 ** (attempt - 1))  # 5s, 10s, 20s
                print(f"    ⚠️ Retry {attempt}/{max_retries} sau {wait_time}s: {type(e).__name__}")
                time.sleep(wait_time)
            else:
                print(f"    ❌ Failed after {max_retries} attempts: {e}")
                return f"[OCR ERROR: {e}]"
    
    return "[OCR ERROR: Max retries exceeded]"
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