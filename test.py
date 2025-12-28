import os
import re
from collections import defaultdict

# ===== CẤU HÌNH =====
INPUT_DIR = "./law"   # thư mục chứa các file txt
OUTPUT_FILE = "duplicate_laws_report.txt"

# ===== HÀM CHUẨN HÓA =====
def normalize_law_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[\/_\-]+", " ", name)   # / _ - -> space
    name = re.sub(r"\s+", " ", name)        # nhiều space -> 1
    return name

# ===== ĐỌC TẤT CẢ FILE =====
law_index = defaultdict(list)
original_names = defaultdict(dict)
# normalized_name -> {filename: original_name}

for filename in os.listdir(INPUT_DIR):
    if not filename.endswith(".txt"):
        continue

    path = os.path.join(INPUT_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            law = line.strip()
            if not law:
                continue

            norm = normalize_law_name(law)
            law_index[norm].append(filename)
            original_names[norm][filename] = law

# ===== PHÂN TÍCH TRÙNG =====
duplicates = {
    norm: files
    for norm, files in law_index.items()
    if len(files) > 1
}

# ===== GHI BÁO CÁO =====
with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
    out.write("BÁO CÁO LUẬT TRÙNG GIỮA CÁC FILE TXT\n")
    out.write("=" * 60 + "\n\n")

    for idx, (norm, files) in enumerate(duplicates.items(), 1):
        keep_file = files[0]
        out.write(f"{idx}. LUẬT TRÙNG:\n")
        out.write(f"   Tên chuẩn: {norm}\n")
        out.write(f"   GIỮ LẠI: {keep_file}\n")
        out.write("   ĐỀ XUẤT XÓA Ở:\n")

        for f in files[1:]:
            out.write(f"     - {f}: {original_names[norm][f]}\n")

        out.write("\n")

print(f"✅ Hoàn thành. Báo cáo được lưu tại: {OUTPUT_FILE}")
