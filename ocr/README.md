# OCR Module

Trích xuất text và bảng từ file PDF văn bản pháp luật.

## Cài đặt

```bash
cd /home/johny02/projects/thuvienphapluat-crawler
uv sync
```

## Scripts

### 1. `extract_tables.py` - Trích xuất bảng (không cần AI)

Dùng pdfplumber để trích xuất bảng từ PDF ra Markdown. **Nhanh và miễn phí!**

```bash
# Trích xuất tất cả trang
uv run python ocr/extract_tables.py ocr/data/file.pdf

# Chỉ định trang bắt đầu
uv run python ocr/extract_tables.py ocr/data/file.pdf -s 3

# Chỉ định khoảng trang
uv run python ocr/extract_tables.py ocr/data/file.pdf -s 3 -e 100

# Chỉ định file output
uv run python ocr/extract_tables.py ocr/data/file.pdf -o output.md
```

**Output:** `file_tables.md` (cùng thư mục với PDF)

---

### 2. `ocr_pdf.py` - OCR với AI model (Qwen3-VL-8B)

Dùng AI model để OCR toàn bộ text, hỗ trợ bảng phức tạp và scan PDF.

```bash
# OCR toàn bộ file
uv run python ocr/ocr_pdf.py ocr/data/file.pdf

# Chỉ định khoảng trang
uv run python ocr/ocr_pdf.py ocr/data/file.pdf -s 0 -e 100
```

**Output:** `file.txt` (cùng thư mục với PDF)

**Lưu ý:** 
- Cần API key HuggingFace trong `.env`
- Có hỗ trợ resume nếu bị gián đoạn
- Tốc độ ~11s/trang với GPU L40S

---

## So sánh

| Phương pháp | Tốc độ | Chi phí | Chất lượng | Dùng khi |
|-------------|--------|---------|------------|----------|
| `extract_tables.py` | ~0.1s/trang | FREE | Tốt với PDF text-layer | Bảng đơn giản |
| `ocr_pdf.py` | ~11s/trang | FREE (HF) | Xuất sắc | PDF scan, bảng phức tạp |
