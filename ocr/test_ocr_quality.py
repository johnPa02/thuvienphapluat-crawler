#!/usr/bin/env python3
"""
Test OCR quality with OpenAI Vision API on sample pages from PDF.
Estimates cost and compares extraction methods.
"""

import base64
import io
import os
import sys

import fitz  # PyMuPDF
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()


def pdf_page_to_image(pdf_path: str, page_num: int, dpi: int = 150) -> bytes:
    """Convert a single PDF page to PNG image bytes."""
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num)
    
    # Render page to image
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    
    # Convert to PIL Image then to bytes
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    doc.close()
    
    return buffer.getvalue()


def extract_text_pymupdf(pdf_path: str, page_num: int) -> str:
    """Extract text from a page using PyMuPDF."""
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num)
    text = page.get_text()
    doc.close()
    return text


def ocr_with_openai(image_bytes: bytes, client: OpenAI, model: str = "gpt-4o-mini") -> dict:
    """OCR image using OpenAI Vision API. Returns text and usage info."""
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
Nếu có bảng, hãy format thành markdown table.
Giữ nguyên định dạng và thứ tự của văn bản.
Không thêm giải thích, chỉ trả về nội dung được trích xuất."""
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}",
                            "detail": "high"  # high detail for better OCR
                        }
                    }
                ]
            }
        ],
        max_tokens=4096
    )
    
    return {
        "text": response.choices[0].message.content,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens
        }
    }


def estimate_cost(total_pages: int, avg_prompt_tokens: int, avg_completion_tokens: int, model: str = "gpt-4o-mini"):
    """Estimate total cost for OCR all pages."""
    # Pricing as of 2024 (per 1M tokens)
    pricing = {
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    }
    
    if model not in pricing:
        model = "gpt-4o-mini"
    
    input_cost = (total_pages * avg_prompt_tokens / 1_000_000) * pricing[model]["input"]
    output_cost = (total_pages * avg_completion_tokens / 1_000_000) * pricing[model]["output"]
    total_cost = input_cost + output_cost
    
    return {
        "model": model,
        "total_pages": total_pages,
        "estimated_input_tokens": total_pages * avg_prompt_tokens,
        "estimated_output_tokens": total_pages * avg_completion_tokens,
        "input_cost_usd": round(input_cost, 4),
        "output_cost_usd": round(output_cost, 4),
        "total_cost_usd": round(total_cost, 4),
        "total_cost_vnd": round(total_cost * 25000, 0)  # Approximate VND
    }


def main():
    pdf_path = "/home/johny02/projects/thuvienphapluat-crawler/ocr/data/Quyet_dinh_3467-QD-BYT.pdf"
    
    # Check if PDF exists
    if not os.path.exists(pdf_path):
        print(f"Error: PDF not found at {pdf_path}")
        sys.exit(1)
    
    # Get total pages
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()
    
    print(f"📄 PDF: {os.path.basename(pdf_path)}")
    print(f"📊 Total pages: {total_pages}")
    print("=" * 60)
    
    # Test pages: first page (text), a table page (around page 3-4), and a middle table page
    test_pages = [0, 2, 100]  # 0-indexed
    
    # Initialize OpenAI client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ Error: OPENAI_API_KEY not found in .env")
        sys.exit(1)
    
    client = OpenAI(api_key=api_key)
    
    total_prompt_tokens = 0
    total_completion_tokens = 0
    
    for page_num in test_pages:
        if page_num >= total_pages:
            continue
            
        print(f"\n🔍 Testing page {page_num + 1}/{total_pages}")
        print("-" * 40)
        
        # Method 1: PyMuPDF text extraction
        print("\n📝 PyMuPDF Text Extraction:")
        pymupdf_text = extract_text_pymupdf(pdf_path, page_num)
        print(pymupdf_text[:500] + "..." if len(pymupdf_text) > 500 else pymupdf_text)
        
        # Method 2: OpenAI Vision OCR
        print("\n🤖 OpenAI Vision OCR (gpt-4o-mini):")
        image_bytes = pdf_page_to_image(pdf_path, page_num)
        image_size_kb = len(image_bytes) / 1024
        print(f"   Image size: {image_size_kb:.1f} KB")
        
        result = ocr_with_openai(image_bytes, client)
        print(f"   Tokens - Prompt: {result['usage']['prompt_tokens']}, Completion: {result['usage']['completion_tokens']}")
        print(f"\n{result['text'][:800]}..." if len(result['text']) > 800 else f"\n{result['text']}")
        
        total_prompt_tokens += result['usage']['prompt_tokens']
        total_completion_tokens += result['usage']['completion_tokens']
    
    # Calculate averages and estimate total cost
    num_tested = len([p for p in test_pages if p < total_pages])
    avg_prompt = total_prompt_tokens // num_tested
    avg_completion = total_completion_tokens // num_tested
    
    print("\n" + "=" * 60)
    print("💰 COST ESTIMATION")
    print("=" * 60)
    
    for model in ["gpt-4o-mini", "gpt-4o"]:
        estimate = estimate_cost(total_pages, avg_prompt, avg_completion, model)
        print(f"\n📌 Model: {model}")
        print(f"   Total pages: {estimate['total_pages']}")
        print(f"   Est. input tokens: {estimate['estimated_input_tokens']:,}")
        print(f"   Est. output tokens: {estimate['estimated_output_tokens']:,}")
        print(f"   Input cost: ${estimate['input_cost_usd']:.4f}")
        print(f"   Output cost: ${estimate['output_cost_usd']:.4f}")
        print(f"   💵 TOTAL: ${estimate['total_cost_usd']:.4f} (~{estimate['total_cost_vnd']:,.0f} VND)")


if __name__ == "__main__":
    main()
