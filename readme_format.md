Sau khi crawl dữ liệu từ `thuvienphapluat.vn`, cần **định dạng lại** để dữ liệu có cấu trúc rõ ràng. File format này chia văn bản thành các đoạn chunk theo **Điều, Phụ lục, Biểu số**, xử lý footnote (đặc biệt với văn bản hợp nhất), và đảm bảo không vượt quá giới hạn token.

---

## Overview

Có hai script định dạng chính:

| Script | Mục đích |
|-------|--------|
| `format.py` | Dành cho **văn bản thông thường**: luật, nghị định, thông tư... |
| `format_hopnhat.py` | Dành riêng cho **văn bản hợp nhất**, có xử lý **footnote** (`[1]`, `[2]`, ...) |

Cả hai script đều:
- Tự động trích tiêu đề từ nội dung hoặc tên file.
- Chia văn bản thành các **chunk** theo đơn vị pháp lý (Điều, Phụ lục, Chương...).
- Gộp các chunk nhỏ hợp lý để tránh phân mảnh.
- Giới hạn kích thước chunk theo **số token** (mặc định: 15.000 token ~ giới hạn API LLM).
- Ghi ra file `.txt` đã chuẩn hóa, mỗi chunk cách nhau bởi dòng trống.

---

## Requirements

- Python 3.8+
- Thư viện: `tiktoken` (dùng để đếm token theo chuẩn OpenAI)
  ```bash
  pip install tiktoken

## Dữ liệu đầu vào: 
Thư mục chứa các file .txt từ quá trình crawl (mỗi file là 1 văn bản pháp luật)

## Cách sử dụng
python format.py --input-dir ./crawl --output-dir ./formatted

python format_hopnhat.py --input-dir ./crawl_hopnhat --output-dir ./formatted_hopnhat

--input-dir: Thư mục chứa file .txt đã crawl
--output-dir: Thư mục lưu kết quả định dạng

## Quy trình xử lý
1. Với format.py (văn bản thường)
- Trích tiêu đề: lấy dòng đầu tiên có nội dung, hoặc suy ra từ tên file.
- Chia chunk theo các mẫu: 
Điều X...
PHỤ LỤC I...
Biểu số 1...
- Kiểm soát token: nếu chunk vượt 15.000 token → chia nhỏ theo đoạn văn.
- Gộp hợp lý: tránh cắt ngang Điều hoặc bảng (|...|).
- Ghi file: mỗi chunk được ghi dưới dạng:
[Tiêu đề]. [Nội dung chunk]
2. Với format_hopnhat.py (văn bản hợp nhất)
Ngoài các bước trên, có thêm xử lý đặc biệt là xử lý footnote (chú thích)
Văn bản hợp nhất thường có dạng như sau:
```
Luật Doanh nghiệp[1] năm 2020...
...
[1] Văn bản hợp nhất số 09/VBHN-VPQH ngày 15/12/2023
```
Script sẽ: 
- Tìm tất cả [n] trong văn bản. 
- Nếu [n] xuất hiện 2 lần trở lên, coi lần đầu là chú thích trong nội dung, lần thứ hai là định nghĩa footnote. 
- Thay thế [1] trong nội dung bằng toàn bộ nội dung footnote:
Luật Doanh nghiệp[Văn bản hợp nhất số 09/VBHN-VPQH...] năm 2020...
- Xóa block footnote ở cuối để tránh nhiễu.

### TODO
- Mất ngữ cảnh khi chia chunk do vượt token: nếu một Điều quá dài (>15.000 token), việc chia nhỏ sẽ mất thông tin về số Điều/khoản, gây khó khăn cho truy xuất sau này.
- Dấu ngoặc kép lồng nhau trong footnote có thể làm hỏng regex
