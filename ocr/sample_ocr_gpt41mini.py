#!/usr/bin/env python3
"""
OCR 5 sample pages with gpt-4.1-mini for comparison.
"""

import base64
import io
import os

import fitz  # PyMuPDF
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()


def pdf_page_to_image(pdf_path: str, page_num: int, dpi: int = 150) -> bytes:
    """Convert a single PDF page to PNG image bytes."""
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    doc.close()
    return buffer.getvalue()


def ocr_with_openai(image_bytes: bytes, client: OpenAI, model: str = "gpt-4.1-mini") -> dict:
    """OCR image using OpenAI Vision API."""
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": """Trích xuất toàn bộ text từ hình ảnh này. 
Đây là văn bản pháp luật Việt Nam, có thể chứa bảng.
Nếu có bảng, hãy format thành markdown table với đầy đủ các cột.
Giữ nguyên định dạng và thứ tự của văn bản.
Không thêm giải thích, chỉ trả về nội dung được trích xuất."""
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}",
                            "detail": "high"
                        }
                    }
                ]
            }
        ],
        max_tokens=4096
    )
    
    return {
        "text": response.choices[0].message.content,
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
    }


def main():
    pdf_path = "/home/johny02/projects/thuvienphapluat-crawler/ocr/data/Quyet_dinh_3467-QD-BYT.pdf"
    output_dir = "/home/johny02/projects/thuvienphapluat-crawler/ocr/data/sample_ocr_gpt41mini"
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Same 5 pages as before for comparison
    sample_pages = [0, 2, 50, 200, 500]  # 0-indexed
    
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()
    
    total_prompt = 0
    total_completion = 0
    
    model = "gpt-4.1-mini"
    print(f"📄 OCR {len(sample_pages)} sample pages using {model}")
    print(f"📁 Output directory: {output_dir}")
    print("=" * 60)
    
    all_results = []
    
    for i, page_num in enumerate(sample_pages):
        if page_num >= total_pages:
            print(f"⚠️ Page {page_num + 1} exceeds total pages, skipping")
            continue
        
        print(f"\n🔄 [{i+1}/{len(sample_pages)}] Processing page {page_num + 1}...")
        
        # Convert to image
        image_bytes = pdf_page_to_image(pdf_path, page_num)
        
        # OCR with gpt-4.1-mini
        result = ocr_with_openai(image_bytes, client, model)
        total_prompt += result["prompt_tokens"]
        total_completion += result["completion_tokens"]
        
        print(f"   ✅ OCR done - Tokens: {result['prompt_tokens']} prompt, {result['completion_tokens']} completion")
        
        # Save OCR result
        txt_path = os.path.join(output_dir, f"page_{page_num + 1:04d}.md")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"# Page {page_num + 1} (gpt-4.1-mini)\n\n")
            f.write(result["text"])
        print(f"   📝 Saved OCR: {txt_path}")
        
        all_results.append({
            "page": page_num + 1,
            "text": result["text"]
        })
    
    # Save combined output
    combined_path = os.path.join(output_dir, "combined_sample.md")
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write(f"# OCR Sample Results ({model})\n\n")
        f.write(f"Sampled pages: {[p+1 for p in sample_pages]}\n\n")
        f.write("---\n\n")
        for r in all_results:
            f.write(f"## Page {r['page']}\n\n")
            f.write(r["text"])
            f.write("\n\n---\n\n")
    
    print("\n" + "=" * 60)
    print("📊 Summary:")
    print(f"   Model: {model}")
    print(f"   Pages processed: {len(all_results)}")
    print(f"   Total prompt tokens: {total_prompt:,}")
    print(f"   Total completion tokens: {total_completion:,}")
    
    # gpt-4.1-mini pricing: input=$0.40/1M, output=$1.60/1M
    cost = (total_prompt / 1_000_000 * 0.40) + (total_completion / 1_000_000 * 1.60)
    print(f"   💰 Cost for these pages: ${cost:.4f}")
    print(f"\n📁 All results saved to: {output_dir}")
    print(f"📄 Combined file: {combined_path}")


if __name__ == "__main__":
    main()
