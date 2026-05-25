#!/usr/bin/env python3
import argparse
import re
from pathlib import Path


_HEADING_RE = re.compile(r'^\s*(Chương|Phần|Mục|Tiểu\s+mục)\b', re.IGNORECASE)


def _is_doc_watermark(line: str) -> bool:
    """Phát hiện chuỗi base64 watermark định danh tài liệu"""
    return bool(re.match(r'^[A-Za-z0-9+/]{10,}={0,2}$', line.strip()))


def _is_metadata_block_start(line: str) -> bool:
    """Phát hiện dòng bắt đầu block metadata cuối tài liệu"""
    s = line.strip()
    return s.startswith('Nơi nhận') or s.startswith('XÁC THỰC') or s.startswith('CHỦ NHIỆM')


def resolve_footnotes(text: str) -> str:
    """
    Giải quyết footnote: chuyển nội dung footnote [n] từ cuối văn bản lên vị trí đánh dấu [n] đầu tiên.
    Khi [n] xuất hiện ở cuối dòng (chapter heading), các dòng tiếp theo trong thân văn bản
    (các điều, khoản) cũng được gộp vào cùng dòng đó cho đến khi gặp watermark hoặc metadata.
    """
    lines = text.splitlines()
    n = len(lines)

    # BƯỚC 1: Tìm tất cả vị trí xuất hiện [n]
    occurrences = {}
    for idx, line in enumerate(lines):
        for m in re.finditer(r'\[(\d+)\]', line):
            num = m.group(1)
            occurrences.setdefault(num, []).append(idx)

    # BƯỚC 2: Xây dựng nội dung footnote và đánh dấu xóa các dòng định nghĩa
    to_delete = set()
    replacements = {}  # num -> footnote content

    for num, positions in occurrences.items():
        if len(positions) < 2:
            continue

        def_idx = positions[1]
        line = lines[def_idx]
        m = re.match(rf'^\s*\[{num}\]\s*(.*)$', line)
        if not m:
            continue

        # Thu thập nội dung qua các dòng trống, dừng khi gặp footnote tiếp theo hoặc hết file
        content_parts = [m.group(1).strip()]
        j = def_idx + 1
        while j < n and not re.match(r'^\s*\[\d+\]\s*', lines[j]):
            if lines[j].strip():
                content_parts.append(lines[j].strip())
            j += 1

        footnote_content = ' '.join(content_parts).strip()
        if footnote_content:
            replacements[num] = footnote_content
            for k in range(def_idx, j):
                to_delete.add(k)

    # BƯỚC 3: Thay thế [n] và gộp các dòng continuation khi [n] ở cuối dòng
    line_modifications = {}   # idx -> dòng đã chỉnh sửa
    continuation_deletes = set()

    for idx, line in enumerate(lines):
        if idx in to_delete:
            continue

        new_line = line
        end_substitution = False

        for num, content in replacements.items():
            if f'[{num}]' in new_line:
                # Chỉ trigger continuation khi [n] ở cuối dòng AND dòng là heading chương/mục
                if re.search(rf'\[{num}\]\s*$', line) and _HEADING_RE.match(line):
                    end_substitution = True
                new_line = re.sub(rf'\[{num}\]', f'[{content}]', new_line)

        line_modifications[idx] = new_line

        # Nếu [n] ở cuối dòng → gộp các dòng tiếp theo vào cùng dòng
        if end_substitution and new_line.rstrip().endswith(']'):
            j = idx + 1
            parts = []

            while j < n and j not in to_delete:
                stripped = lines[j].strip()

                if not stripped:
                    continuation_deletes.add(j)
                    j += 1
                    continue

                if _is_doc_watermark(stripped) or _is_metadata_block_start(stripped):
                    # Xóa toàn bộ watermark + metadata cho đến định nghĩa footnote
                    while j < n and j not in to_delete:
                        if re.match(r'^\s*\[\d+\]\s*', lines[j]):
                            break
                        continuation_deletes.add(j)
                        j += 1
                    break

                if re.match(r'^\s*\[\d+\]\s*', stripped):
                    break

                parts.append(stripped)
                continuation_deletes.add(j)
                j += 1

            if parts:
                base = line_modifications[idx].rstrip()
                # Chèn nội dung continuation vào bên trong dấu ']' cuối
                line_modifications[idx] = base[:-1] + ' ' + ' '.join(parts) + ']'

    # BƯỚC 4: Xây dựng output
    all_deletes = to_delete | continuation_deletes
    result_lines = []
    for idx in range(n):
        if idx in all_deletes:
            continue
        result_lines.append(line_modifications.get(idx, lines[idx]))

    return '\n'.join(result_lines)


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