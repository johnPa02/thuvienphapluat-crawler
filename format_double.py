import os
import argparse
import re

def clean_text(text):
    lines = text.splitlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # Kiểm tra xem dòng hiện tại có "chỉ chứa 'Điều'" (cho phép có dấu ngoặc kép mở ở đầu)
        # Ví dụ hợp lệ: "Điều", "“Điều"
        stripped = line.strip()
        if re.fullmatch(r'[“"]?Điều', stripped):
            # Dòng tiếp theo tồn tại và bắt đầu bằng số + dấu chấm?
            if i + 1 < len(lines):
                next_line = lines[i + 1].lstrip()
                if re.match(r'^\d+\.', next_line):
                    # Gộp: "“Điều" + "14. ..." → "“Điều 14. ..."
                    combined = line.rstrip() + " " + next_line
                    new_lines.append(combined)
                    i += 2  # bỏ qua dòng tiếp theo
                    continue

        # Nếu không phải trường hợp đặc biệt, giữ nguyên dòng
        new_lines.append(line)
        i += 1

    # Bây giờ xử lý các số điều bị dính vào cuối dòng: "...nội dung 1. Tiếp theo..."
    # → tách thành: "...nội dung\n1. Tiếp theo..."
    final_lines = []
    for line in new_lines:
        # Tìm tất cả vị trí có " <digit>." không ở đầu dòng
        # Nhưng tránh tách trong cụm như "Điều 14." (đã được gộp rồi nên an toàn)
        # Ta chỉ tách khi pattern xuất hiện **sau ít nhất 1 ký tự không phải "Điều\s\d"**
        # Đơn giản: thay " <digit>." bằng "\n<d>." nếu không nằm trong "Điều <d>."
        
        # Tách an toàn: chỉ tách nếu trước đó KHÔNG phải là "Điều"
        # Dùng regex với negative lookbehind không khả thi → thay bằng cách:
        # - Nếu dòng bắt đầu bằng "Điều \d+." → giữ nguyên
        if re.match(r'^[“"]?Điều\s+\d+\.', line):
            final_lines.append(line)
        else:
            # Tách các " <digit>." thành xuống dòng
            # Ví dụ: "abc 1. def 2. xyz" → "abc\n1. def\n2. xyz"
            # Dùng re.sub để chèn \n trước mỗi " <digit>."
            fixed = re.sub(r' (\d+\.\s*)', r'\n\1', line)
            # Chia nhỏ rồi thêm từng phần
            for part in fixed.splitlines():
                final_lines.append(part)
    
    # Loại bỏ dòng trống thừa (tuỳ chọn)
    # final_lines = [line for line in final_lines if line.strip() != '']
    
    return "\n".join(final_lines)

def process_file(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    cleaned_content = clean_text(content)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(cleaned_content)

def main():
    parser = argparse.ArgumentParser(description='Format crawled files by fixing broken line breaks in legal clauses')
    parser.add_argument('--input-dir', default='bhyt_format', help='Input directory with crawled .txt files')
    parser.add_argument('--output-dir', default='bhyt_double', help='Output directory for formatted files')
    args = parser.parse_args()

    input_dir = args.input_dir
    output_dir = args.output_dir

    if not os.path.exists(input_dir):
        print(f"Input directory '{input_dir}' does not exist.")
        return

    for filename in os.listdir(input_dir):
        if filename.endswith('.txt'):
            input_path = os.path.join(input_dir, filename)
            output_path = os.path.join(output_dir, filename)
            print(f"Processing {input_path} -> {output_path}")
            process_file(input_path, output_path)

    print("Formatting completed.")

if __name__ == '__main__':
    main()