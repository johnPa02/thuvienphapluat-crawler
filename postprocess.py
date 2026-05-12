import re


def postprocess(input_file: str, output_file: str, doc_name: str = "Nghị định 47/2021/NĐ-CP") -> None:
    """
    Postprocess văn bản pháp luật:
    - Thêm dòng trống trước mỗi Chương, Mục, Điều
    - Thêm tên văn bản trước mỗi Điều
    - Không thêm dòng trống giữa Chương/Mục và Điều ngay sau nó
    
    Args:
        input_file: File input (output.txt)
        output_file: File output sau khi xử lý
        doc_name: Tên văn bản pháp luật
    """
    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Bỏ các dấu chấm đứng một mình trên một dòng
    content = re.sub(r'\n\.\n', '\n', content)
    
    # Bỏ "[Click vào để xem nội dung]"
    content = content.replace(' [Click vào để xem nội dung]', '')
    content = content.replace('[Click vào để xem nội dung]', '')
    
    # # Tách số khoản (1. 2. 3. ...) ra dòng mới khi bị dính vào ] hoặc cuối câu trước
    # # Pattern: "] 1." hoặc "câu gì đó 1." -> xuống dòng trước số
    # content = re.sub(r'\]\s+(\d+\.)\s*\n', r']\n\1\n', content)
    # content = re.sub(r'\]\s+(\d+\.)\s+', r']\n\1 ', content)
    
    # Thêm dòng trống trước Chương (1 dòng trống = 1 newline)
    content = re.sub(r'(Chương\s+[IVXLCDM]+)', r'\n\1', content)
    
    # Thêm dòng trống trước Mục
    content = re.sub(r'(Mục\s+\d+\.)', r'\n\1', content)
    
    # # Thêm dòng trống trước các mục I. II. III. ... (đầu dòng, theo sau là chữ in hoa)
    # content = re.sub(r'\n((?:I|II|III|IV|V|VI|VII|VIII|IX|X)\.\s+[A-Z])', r'\n\n\1', content)
    
    # Thêm dòng trống và tên văn bản trước mỗi Điều (1 dòng trống)
    content = re.sub(r'(Điều\s+\d+\.)', rf'\n{doc_name}. \1', content)
    
    # Xử lý trường hợp Chương/Mục ngay trước Điều: bỏ dòng trống thừa giữa chúng
    # Pattern: Chương ... \n\n Nghị định -> Chương ... \n Nghị định
    content = re.sub(r'(Chương\s+[IVXLCDM]+[^\n]*)\n+(' + re.escape(doc_name) + r')', r'\1\n\2', content)
    content = re.sub(r'(Mục\s+\d+\.[^\n]*)\n+(' + re.escape(doc_name) + r')', r'\1\n\2', content)
    
    # Loại bỏ các dòng trống thừa ở đầu file
    processed = content.lstrip('\n')
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(processed)
    
    print(f"Đã xử lý xong: {input_file} -> {output_file}")


def main():
    input_file = "output.txt"
    output_file = "output_processed.txt"
    doc_name = "Nghị định 47/2021/NĐ-CP"
    
    postprocess(input_file, output_file, doc_name)


if __name__ == "__main__":
    main()
