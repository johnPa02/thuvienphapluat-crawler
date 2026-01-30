import re

INPUT_FILE = "/root/tvpl/Quyet_dinh_3500-QĐ-BYT.txt"
OUTPUT_FILE = "output_test_3.txt"

TITLE = """Quyết định 3467/QĐ-BYT ngày 15/11/2024 về việc giá dịch vụ khám bệnh, chữa bệnh áp dụng tại Bệnh viện Nhi Trung ương.
Phụ lục III
GIÁ DỊCH VỤ KỸ THUẬT VÀ XÉT NGHIỆM
(Ban hành kèm theo Quyết định số 3467/QĐ-BYT ngày 15/11/2024 của Bộ Y tế)
Đơn vị: đồng
"""

TABLE_HEADER = """| STT | Mã tương đương | Tên danh mục kỹ thuật theo Thông tư 23/2024/TT-BYT | Tên dịch vụ phê duyệt giá | Mức giá | Ghi chú |
| --- | --- | --- | --- | --- | --- |
| A | Danh mục dịch vụ do Quỹ BHYT thanh toán |  |  |  |  |
"""

page_count = 0
groups = []
current_rows = []

with open(INPUT_FILE, encoding="utf-8") as f:
    for line in f:
        line = line.rstrip()

        # Đếm PAGE
        if line.startswith("# PAGE"):
            page_count += 1
            if page_count % 5 == 1 and current_rows:
                groups.append(current_rows)
                current_rows = []
            continue

        # Lấy dòng dữ liệu bảng
        if re.match(r"\|\s*\d+\s*\|", line):
            current_rows.append(line)

# Thêm cụm cuối
if current_rows:
    groups.append(current_rows)

# Ghi output
with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
    for i, group in enumerate(groups):
        out.write(TITLE)
        out.write(TABLE_HEADER)
        for row in group:
            out.write(row + "\n")

        # cách 1 dòng giữa các cụm
        if i != len(groups) - 1:
            out.write("\n")
