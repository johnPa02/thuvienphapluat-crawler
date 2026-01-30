import re
from pathlib import Path
from typing import List, Optional


# ======================
# CONFIG
# ======================
GLOBAL_TITLE = (
    """Thông tư 32/2023/TT-BYT hướng dẫn Luật Khám bệnh, chữa bệnh do Bộ trưởng Bộ Y tế ban hành"""
)

PAGE_RE = re.compile(r'^#\s*PAGE\s+\d+', re.IGNORECASE)

APPENDIX_RE = re.compile(
    r'^\s*phụ\s+lục(?:\s+số)?\s+\d+.*',
    re.IGNORECASE
)

TABLE_SEPARATOR_RE = re.compile(r'^\|\s*-+')
TABLE_LINE_RE = re.compile(r'^\|.*\|$')


# ======================
# DATA STRUCTURES
# ======================
class AppendixState:
    def __init__(self):
        self.title_lines: List[str] = []
        self.table_header: List[str] = []
        self.table_rows: List[str] = []

    def has_data(self) -> bool:
        return bool(self.table_header and self.table_rows)

    def reset_table(self):
        self.table_header = []
        self.table_rows = []


# ======================
# HELPERS
# ======================
def is_appendix_line(line: str) -> bool:
    return bool(APPENDIX_RE.match(line))


def is_table_line(line: str) -> bool:
    return bool(TABLE_LINE_RE.match(line))


# ======================
# CORE LOGIC
# ======================
def process_file(text: str) -> List[str]:
    lines = [l.rstrip() for l in text.splitlines()]

    chunks: List[str] = []
    current_appendix: Optional[AppendixState] = None

    current_page_no = None
    pages_in_chunk = 0

    def flush_chunk(reset_appendix: bool = False):
        nonlocal pages_in_chunk

        if not current_appendix or not current_appendix.has_data():
            pages_in_chunk = 0
            return

        block = []
        block.append(GLOBAL_TITLE)
        block.extend(current_appendix.title_lines)
        block.extend(current_appendix.table_header)
        block.extend(current_appendix.table_rows)

        chunks.append("\n".join(block))
        pages_in_chunk = 0

        if reset_appendix:
            return

        current_appendix.table_rows = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # -------- PAGE --------
        if PAGE_RE.match(line):
            page_no = int(re.search(r'\d+', line).group())
            if current_page_no is None or page_no != current_page_no:
                current_page_no = page_no
                pages_in_chunk += 1
                if pages_in_chunk > 10:
                    flush_chunk(reset_appendix=False)
            i += 1
            continue

        if line.startswith("===="):
            i += 1
            continue

        # -------- PHỤ LỤC --------
        if is_appendix_line(line):
            flush_chunk(reset_appendix=True)

            current_appendix = AppendixState()
            current_appendix.title_lines.append(line)

            i += 1
            while i < len(lines):
                l = lines[i].strip()
                if not l or is_table_line(l) or PAGE_RE.match(l):
                    break
                current_appendix.title_lines.append(l)
                i += 1
            continue

        # -------- TABLE HEADER --------
        if (
            is_table_line(line)
            and i + 1 < len(lines)
            and TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            # ⚠️ TRƯỜNG HỢP KHÔNG CÓ PHỤ LỤC
            if current_appendix is None:
                current_appendix = AppendixState()

                # lấy tiêu đề phía trên bảng
                k = i - 1
                title_buf = []
                while k >= 0:
                    prev = lines[k].strip()
                    if not prev or PAGE_RE.match(prev) or prev.startswith("===="):
                        break
                    title_buf.append(prev)
                    k -= 1

                current_appendix.title_lines = list(reversed(title_buf))

            if not current_appendix.table_header:
                current_appendix.table_header = [line, lines[i + 1]]

            i += 2
            continue

        # -------- TABLE ROW --------
        if current_appendix and is_table_line(line):

            if current_appendix.table_header and line == current_appendix.table_header[0]:
                i += 1
                continue

            if TABLE_SEPARATOR_RE.match(line):
                i += 1
                continue

            current_appendix.table_rows.append(line)
            i += 1
            continue

        i += 1

    flush_chunk(reset_appendix=True)
    return chunks



# ======================
# OUTPUT
# ======================
def write_single_file(chunks: List[str], output_file: Path):
    output_file.write_text("\n\n".join(chunks), encoding="utf-8")


# ======================
# ENTRY POINT
# ======================
def main(input_txt: str, output_txt: str):
    text = Path(input_txt).read_text(encoding="utf-8")
    chunks = process_file(text)
    write_single_file(chunks, Path(output_txt))


if __name__ == "__main__":
    main(
        "ocr/pdf/Thông_tư_32.txt",
        "ocr/data_2/Thông_tư_32.txt"
    )
