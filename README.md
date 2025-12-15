# ThưViệnPhápLuật Crawler

Công cụ crawl và xử lý văn bản pháp luật từ thuvienphapluat.vn

## Tính năng

- ✅ Crawl nội dung văn bản pháp luật
- ✅ Trích xuất nội dung hover tooltip (sửa đổi, bãi bỏ, hướng dẫn)
- ✅ Tự động format với tên văn bản trước mỗi Điều
- ✅ Tự động phát hiện tên văn bản từ URL
- ✅ Hỗ trợ đăng nhập bằng cookies

## Cài đặt

```bash
# Clone repo
git clone <repo-url>
cd thuvienphapluat-crawler

# Cài đặt dependencies với uv
uv sync

# Cài đặt Playwright browsers
uv run playwright install chromium
```

## Sử dụng

### Pipeline hoàn chỉnh (khuyên dùng)

```bash
# Crawl một văn bản
uv run python pipeline.py "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-47-2021-ND-CP-huong-dan-Luat-Doanh-nghiep-470561.aspx"

# Chỉ định file output
uv run python pipeline.py "https://thuvienphapluat.vn/van-ban/..." -o "nghi_dinh_47.txt"

# Chỉ định tên văn bản thủ công
uv run python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --doc-name "Luật ABC 2024"

# Xem help
uv run python pipeline.py --help
```

### Sử dụng riêng từng module

```bash
# Chỉ crawl (không postprocess)
uv run python main.py

# Chỉ postprocess (từ output.txt có sẵn)
uv run python postprocess.py
```

## Cookies (để lấy tooltip)

Để lấy được nội dung tooltip (thông tin sửa đổi, bãi bỏ...), bạn cần có tài khoản Pro trên thuvienphapluat.vn.

1. Đăng nhập vào thuvienphapluat.vn trên trình duyệt
2. Export cookies bằng extension [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
3. Lưu file `cookies.txt` vào thư mục project

## Output

Văn bản được format với:
- Tên văn bản trước mỗi Điều: `Nghị định 47/2021/NĐ-CP. Điều 1. ...`
- Thông tin tooltip trong dấu `[]`: `[Điều này bị bãi bỏ bởi...]`
- Phân tách Chương, Mục, Điều rõ ràng

## Cấu trúc project

```
thuvienphapluat-crawler/
├── pipeline.py      # Pipeline hoàn chỉnh (khuyên dùng)
├── main.py          # Module crawl
├── postprocess.py   # Module xử lý text
├── cookies.txt      # File cookies (tự tạo)
├── output.txt       # Output thô
└── output_processed.txt  # Output đã xử lý
```
