#!/usr/bin/env python3
"""
Test OCR with Qwen3-VL-30B on HuggingFace.
"""

import base64
import io
import os
import fitz
from openai import OpenAI
from PIL import Image

# Qwen3 VL 30B on HuggingFace
client = OpenAI(
    base_url=os.getenv('QWEN3_30B_BASE_URL', 'https://b9q7ifg75v5zmz40.us-east-1.aws.endpoints.huggingface.cloud/v1/'),
    api_key=os.getenv('QWEN_API_KEY', '')
)
MODEL = "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8"

pdf_path = 'ocr/data/Quyet_dinh_3467-QD-BYT.pdf'
output_dir = 'ocr/data/sample_ocr_qwen3_30b'
os.makedirs(output_dir, exist_ok=True)

# Test same pages as before
test_pages = [0, 2, 12, 50, 200]  # 0-indexed

doc = fitz.open(pdf_path)
all_results = []

for page_num in test_pages:
    print("=" * 60)
    print(f"Testing page {page_num + 1} with {MODEL}...")
    
    page = doc.load_page(page_num)
    mat = fitz.Matrix(150 / 72, 150 / 72)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    image_bytes = buffer.getvalue()
    
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{base64_image}'}},
                    {'type': 'text', 'text': '''Trích xuất toàn bộ text từ hình ảnh này. 
Đây là văn bản pháp luật Việt Nam, có thể chứa bảng.
Nếu có bảng, hãy format thành markdown table với đầy đủ các cột.
Giữ nguyên định dạng và thứ tự của văn bản.
Không thêm giải thích, chỉ trả về nội dung được trích xuất.'''}
                ]
            }],
            max_tokens=4096
        )
        
        result = response.choices[0].message.content
        print(f"Page {page_num + 1} result (first 1000 chars):")
        print(result[:1000])
        print()
        
        # Save result
        txt_path = os.path.join(output_dir, f"page_{page_num + 1:04d}.md")
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"# Page {page_num + 1} (Qwen3-VL-30B)\n\n")
            f.write(result)
        print(f"Saved to {txt_path}")
        
        all_results.append({
            "page": page_num + 1,
            "text": result
        })
            
    except Exception as e:
        print(f"Error on page {page_num + 1}: {e}")

doc.close()

# Save combined
combined_path = os.path.join(output_dir, "combined_sample.md")
with open(combined_path, 'w', encoding='utf-8') as f:
    f.write(f"# OCR Sample Results ({MODEL})\n\n")
    f.write(f"Sampled pages: {[p+1 for p in test_pages]}\n\n")
    f.write("---\n\n")
    for r in all_results:
        f.write(f"## Page {r['page']}\n\n")
        f.write(r["text"])
        f.write("\n\n---\n\n")

print("\n" + "=" * 60)
print(f"Done! Results saved to {output_dir}/")
print(f"Combined file: {combined_path}")
