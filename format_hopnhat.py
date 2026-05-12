#!/usr/bin/env python3
import argparse
import re
from pathlib import Path


def resolve_footnotes(text: str) -> str:
    """
    Giải quyết footnote: chuyển nội dung footnote [n] từ cuối văn bản lên vị trí đánh dấu [n] đầu tiên
    Ví dụ:
        Input:
            Văn bản có footnote [1] tại đây.
            ...
            [1] Nội dung footnote giải thích thêm.
        
        Output:
            Văn bản có footnote [Nội dung footnote giải thích thêm] tại đây.
    """
    lines = text.splitlines()
    n = len(lines)

    # BƯỚC 1: Tìm tất cả vị trí xuất hiện [n]
    occurrences = {}
    for idx, line in enumerate(lines):
        for m in re.finditer(r'\[(\d+)\]', line):
            num = m.group(1)
            occurrences.setdefault(num, []).append(idx)

    # BƯỚC 2: Xử lý từng footnote
    to_delete = set()
    replacements = {}  # num -> footnote content

    for num, positions in occurrences.items():
        if len(positions) < 2:
            continue  # Chỉ xử lý khi có ít nhất 2 lần xuất hiện (đánh dấu + định nghĩa)

        # Lấy vị trí định nghĩa footnote (thường là lần xuất hiện thứ 2 trở đi)
        def_idx = positions[1]
        line = lines[def_idx]
        m = re.match(rf'^\s*\[{num}\]\s*(.*)$', line)
        if not m:
            continue

        # Trích xuất toàn bộ nội dung footnote (kể cả các dòng tiếp theo không trống)
        content_parts = [m.group(1).strip()]
        j = def_idx + 1
        while j < n and lines[j].strip() and not re.match(r'^\s*\[\d+\]\s*', lines[j]):
            content_parts.append(lines[j].strip())
            j += 1

        footnote_content = ' '.join(content_parts).strip()
        if footnote_content:
            replacements[num] = footnote_content
            # Đánh dấu xóa block footnote gốc
            for k in range(def_idx, j):
                to_delete.add(k)

    # BƯỚC 3: Thay thế [n] bằng nội dung footnote tại vị trí đầu tiên
    new_lines = []
    for idx, line in enumerate(lines):
        if idx in to_delete:
            continue
        # Thay thế tất cả [n] trong dòng bằng nội dung footnote tương ứng
        for num, content in replacements.items():
            line = re.sub(rf'\[{num}\]', f'[{content}]', line)
        new_lines.append(line)

    return '\n'.join(new_lines)


def format_file(src_path: Path, out_dir: Path) -> Path:
    """Chỉ resolve footnote, giữ nguyên toàn bộ cấu trúc văn bản gốc"""
    text = src_path.read_text(encoding='utf-8')
    resolved_text = resolve_footnotes(text)
    
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / src_path.name
    out_path.write_text(resolved_text, encoding='utf-8')
    
    return out_path


def main():
    parser = argparse.ArgumentParser(description='Resolve footnotes only: move footnote content [n] from bottom to inline position')
    parser.add_argument('--input-dir', default='format/dat_dai', help='Input directory with formatted .txt files')
    parser.add_argument('--output-dir', default='format/dat_dai_hn', help='Output directory for footnote-resolved files')
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Input dir not found: {input_dir}")
        return

    txt_files = sorted([p for p in input_dir.glob('*.txt') if p.name.lower() != 'failed_urls.txt'])
    if not txt_files:
        print(f"No .txt files found in {input_dir}")
        return

    success_count = 0
    for p in txt_files:
        try:
            out_path = format_file(p, output_dir)
            print(f"✅ Resolved footnotes for {p.name} -> {out_path}")
            success_count += 1
        except Exception as e:
            print(f"❌ Error processing {p.name}: {e}")

    print('\n' + '='*50)
    print(f"✅ Successfully processed {success_count}/{len(txt_files)} files")
    print(f"📁 Output files written to: {output_dir}")


if __name__ == '__main__':
    main()