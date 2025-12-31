#!/usr/bin/env python3
import argparse
import re
import tiktoken
from pathlib import Path
from typing import List


def extract_title(text: str, filename: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    name = Path(filename).stem
    name = name.replace('_', ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def clean_chunk_lines(chunk: str, title_pattern: str) -> str:
    lines = chunk.splitlines()
    cleaned_lines = []
    noise_re = re.compile(rf'^\s*{title_pattern}\s*\.?\s*$', re.IGNORECASE | re.UNICODE)

    for line in lines:
        if noise_re.fullmatch(line):
            continue
        cleaned_lines.append(line)

    return '\n'.join(cleaned_lines)

def _split_subchunks(text: str) -> List[str]:
    matches = []

    # Điều
    dieu_re = re.compile(r'^\s*Điều\s+(\d+\w*)\s*[.:]', re.IGNORECASE | re.UNICODE | re.MULTILINE)
    for m in dieu_re.finditer(text):
        matches.append(m.start())

    # Phụ lục
    phu_luc_re = re.compile(r'(?<!\w)PHỤ LỤC\s+([IVXLCDM]+)(?=\s|$)', re.UNICODE)
    for m in phu_luc_re.finditer(text):
        matches.append(m.start())

    # Biểu số
    bieu_so_re = re.compile(r'(?<!\w)Biểu số\s+(\d+)\s*:', re.IGNORECASE | re.UNICODE)
    for m in bieu_so_re.finditer(text):
        matches.append(m.start())

    matches = sorted(set(matches))

    if not matches:
        return [text.strip()]

    chunks = []
    if matches[0] > 0:
        preamble = text[:matches[0]].strip()
        if preamble:
            chunks.append(preamble)

    for i in range(len(matches)):
        start = matches[i]
        end = matches[i + 1] if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

    return chunks
def split_into_chunks(text: str) -> List[str]:
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Bước 1: Tìm các vị trí bắt đầu CHƯƠNG
    chuong_re = re.compile(r'^\s*Chương\s+([IVXLCDM\d]+)\b', re.IGNORECASE | re.UNICODE | re.MULTILINE)
    chuong_matches = [(m.start(), m.group(0)) for m in chuong_re.finditer(text)]

    # Nếu có chương → chia theo chương
    if chuong_matches:
        chunks = []
        # Tiền chương (nếu có)
        if chuong_matches[0][0] > 0:
            preamble = text[:chuong_matches[0][0]].strip()
            if preamble:
                # Chia preamble theo Điều/PL/Biểu nếu cần
                chunks.extend(_split_subchunks(preamble))

        # Duyệt từng chương
        for i in range(len(chuong_matches)):
            start = chuong_matches[i][0]
            end = chuong_matches[i + 1][0] if i + 1 < len(chuong_matches) else len(text)
            chuong_text = text[start:end].strip()
            if chuong_text:
                chunks.extend(_split_subchunks(chuong_text))
        return chunks

    # Không có chương → chia theo Điều/Phụ lục/Biểu số như cũ
    return _split_subchunks(text)

def _get_token_encoder():
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        try:
            return tiktoken.get_encoding("gpt2")
        except Exception:
            return None


def _count_tokens(text: str, encoder) -> int:
    if not text:
        return 0
    if encoder:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    return len(text.split())


def _split_by_token_limit(text: str, encoder, max_tokens: int = 15000) -> List[str]:
    if not text:
        return []
    if _count_tokens(text, encoder) <= max_tokens:
        return [text.strip()]

    parts = re.split(r'\n\s*\n', text)
    out = []
    cur = ''

    for p in parts:
        p = p.strip()
        if not p:
            continue

        if not cur:
            if _count_tokens(p, encoder) <= max_tokens:
                cur = p
            else:
                lines = [ln for ln in p.splitlines() if ln.strip()]
                cur2 = ''
                for ln in lines:
                    cand = (cur2 + '\n' + ln) if cur2 else ln
                    if _count_tokens(cand, encoder) <= max_tokens:
                        cur2 = cand
                    else:
                        if cur2:
                            out.append(cur2.strip())
                        cur2 = ln
                if cur2:
                    out.append(cur2.strip())
                cur = ''
        else:
            cand = cur + '\n\n' + p
            if _count_tokens(cand, encoder) <= max_tokens:
                cur = cand
            else:
                out.append(cur.strip())
                if _count_tokens(p, encoder) <= max_tokens:
                    cur = p
                else:
                    lines = [ln for ln in p.splitlines() if ln.strip()]
                    cur2 = ''
                    for ln in lines:
                        cand2 = (cur2 + '\n' + ln) if cur2 else ln
                        if _count_tokens(cand2, encoder) <= max_tokens:
                            cur2 = cand2
                        else:
                            if cur2:
                                out.append(cur2.strip())
                            cur2 = ln
                    if cur2:
                        out.append(cur2.strip())
                    cur = ''

    if cur:
        out.append(cur.strip())
    return out

def resolve_footnotes(text: str) -> str:
    lines = text.splitlines()
    n = len(lines)

    # Lưu: num -> (first_pos, second_pos)
    occurrences = {}
    i = 0

    # --- BƯỚC 1: tìm vị trí xuất hiện [n] ---
    while i < n:
        for m in re.finditer(r'\[(\d+)\]', lines[i]):
            num = m.group(1)
            occurrences.setdefault(num, []).append(i)
        i += 1

    # --- BƯỚC 2: xử lý từng footnote ---
    to_delete = set()

    for num, positions in occurrences.items():
        if len(positions) < 2:
            continue

        first_idx = positions[0]
        second_idx = positions[1]

        # --- BƯỚC 2.1: lấy nội dung footnote ở lần thứ 2 ---
        line = lines[second_idx]
        m = re.match(rf'^\s*\[{num}\]\s*(.*)$', line)
        if not m:
            continue  # không đúng format -> bỏ

        content_parts = [m.group(1)]
        j = second_idx + 1

        while j < n:
            if not lines[j].strip():
                break
            if re.match(r'^\s*\[\d+\]\s*', lines[j]):
                break
            content_parts.append(lines[j])
            j += 1

        footnote_content = '\n'.join(content_parts).strip()

        # --- BƯỚC 2.2: thay [n] ở lần xuất hiện đầu tiên ---
        lines[first_idx] = re.sub(
            rf'\[{num}\]',
            f'[{footnote_content}]',
            lines[first_idx],
            count=1
        )

        # --- BƯỚC 2.3: đánh dấu xoá block footnote ---
        for k in range(second_idx, j):
            to_delete.add(k)

    # --- BƯỚC 3: dựng lại văn bản ---
    result = []
    for idx, line in enumerate(lines):
        if idx not in to_delete:
            result.append(line)

    return '\n'.join(result)

def format_file(src_path: Path, out_dir: Path):
    text = src_path.read_text(encoding='utf-8')
    text = resolve_footnotes(text)  
    title = extract_title(text, src_path.name)
    chunks = split_into_chunks(text)
    merged_chunks = []
    i = 0
    while i < len(chunks):
        current = chunks[i].strip()
        if not current:
            i += 1
            continue

        # Kiểm tra xem chunk hiện tại có phải là "Chương"
        is_chuong = re.match(r'^\s*Chương\s+[IVXLCDM\d]+\b', current, re.IGNORECASE | re.UNICODE) is not None

        if is_chuong and i + 1 < len(chunks):
            next_chunk = chunks[i + 1].strip()
            # Kiểm tra xem chunk tiếp theo có phải là "Điều"
            is_next_dieu = re.match(r'^\s*Điều\s+\d+\w*\b', next_chunk, re.IGNORECASE | re.UNICODE) is not None

            if is_next_dieu:
                # Gộp: giữ nguyên dòng Chương, thêm nội dung Điều (bỏ dòng tiêu đề Điều nếu trùng? Không cần, giữ nguyên)
                merged = current + '\n' + next_chunk
                merged_chunks.append(merged)
                i += 2  # bỏ qua cả chunk tiếp theo
                continue

        # Nếu không gộp, thêm chunk hiện tại
        merged_chunks.append(current)
        i += 1

    # --- Tiếp tục xử lý như cũ ---
    title_pattern = re.escape(title.strip())
    cleaned_chunks = []
    for chunk in merged_chunks:
        c = chunk.strip()
        if not c:
            continue
        c_cleaned = clean_chunk_lines(c, title_pattern).strip()
        if c_cleaned:
            cleaned_chunks.append(c_cleaned)

    chunks = cleaned_chunks
    title_pattern = re.escape(title.strip())

    cleaned_chunks = []
    for chunk in chunks:
        c = chunk.strip()
        if not c:
            continue
        c_cleaned = clean_chunk_lines(c, title_pattern).strip()
        if c_cleaned:
            cleaned_chunks.append(c_cleaned)

    chunks = cleaned_chunks
    out_dir.mkdir(parents=True, exist_ok=True)
    master_name = src_path.stem + '.txt'
    master_path = out_dir / master_name

    encoder = _get_token_encoder()
    total_subchunks = 0

    with master_path.open('w', encoding='utf-8') as mf:
        for chunk in chunks:
            subchunks = _split_by_token_limit(chunk, encoder, max_tokens=15000)
            merged = []
            for sc in subchunks:
                s = sc.strip()
                if not s:
                    continue
                first_line = s.splitlines()[0].strip() if s.splitlines() else ''
                is_table = first_line.startswith('|')
                is_dieu = re.match(r'^\s*Điều\s+\d+\w*\b', first_line, re.I) is not None

                if (is_table or is_dieu) and merged:
                    merged[-1] = merged[-1].rstrip() + '\n' + s
                else:
                    merged.append(s)

            for sc in merged:
                sc_clean = re.sub(r'\n\s*\n\s*(\|)', r'\n\1', sc)
                mf.write(title.rstrip('.') + '. ' + sc_clean.strip() + '\n\n')
                total_subchunks += 1

    return master_path, total_subchunks


def main():
    parser = argparse.ArgumentParser(description='Format crawled files by splitting into Điều chunks')
    parser.add_argument('--input-dir', default='crawl_fix_29_12', help='Input directory with crawled .txt files')
    parser.add_argument('--output-dir', default='formatted_fix_29_12_2', help='Output directory for formatted files')
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

    for p in txt_files:
        try:
            master_path, nchunks = format_file(p, output_dir)
            print(f"Formatted {p.name}: {nchunks} chunks -> {master_path}")
        except Exception as e:
            print(f"Error formatting {p.name}: {e}")

    print('\nDone. Formatted files written to:', output_dir)


if __name__ == '__main__':
    main()