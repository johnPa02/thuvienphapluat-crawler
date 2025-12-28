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


def split_into_chunks(text: str) -> List[str]:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    matches = []

    dieu_re = re.compile(r'^\s*Điều\s+(\d+\w*)\s*[.:]', re.IGNORECASE | re.UNICODE | re.MULTILINE)
    for m in dieu_re.finditer(text):
        matches.append(m.start())

    phu_luc_re = re.compile(r'(?<!\w)PHỤ LỤC\s+([IVXLCDM]+)(?=\s|$)', re.UNICODE)
    for m in phu_luc_re.finditer(text):
        matches.append(m.start())

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
import re
def resolve_footnotes(text: str) -> str:
    lines = text.splitlines()
    main_lines = []
    footnotes = {}  # num -> content
    i = 0

    # 1. Extract footnotes
    while i < len(lines):
        line = lines[i]

        # Match: [1] Nội dung...
        m = re.match(r'^\s*\[(\d+)\]\s*(.*)', line)
        if m:
            num = m.group(1)
            content_lines = [m.group(2)]
            i += 1

            # Collect multiline footnote
            while i < len(lines):
                next_line = lines[i]
                if re.match(r'^\s*\[\d+\]', next_line):
                    break
                content_lines.append(next_line)
                i += 1

            footnotes[num] = "\n".join(content_lines).rstrip()
        else:
            main_lines.append(line)
            i += 1

    main_text = "\n".join(main_lines)

    # 2. Replace [1], [2] everywhere (including nước[1])
    def replace_fn(match):
        num = match.group(1)
        if num in footnotes:
            return f"[{footnotes[num]}]"
        return match.group(0)

    main_text = re.sub(r'\[(\d+)\]', replace_fn, main_text)

    return main_text
def format_file(src_path: Path, out_dir: Path):
    text = src_path.read_text(encoding='utf-8')
    text = resolve_footnotes(text)  
    title = extract_title(text, src_path.name)
    chunks = split_into_chunks(text)
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
    parser.add_argument('--input-dir', default='crawl_fix', help='Input directory with crawled .txt files')
    parser.add_argument('--output-dir', default='formatted_hop_nhat', help='Output directory for formatted files')
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