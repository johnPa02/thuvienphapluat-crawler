#!/usr/bin/env python3
"""
Extract tables from PDF or Excel to Markdown.
- PDF: pdfplumber
- Excel: pandas (xls/xlsx), multi-sheet, legal annex aware
"""

import argparse
import os
from pathlib import Path

# import pdfplumber
import pandas as pd

DOCUMENT_TITLE = ("""Quyết định 4469/QĐ-BYT năm 2020 về "Bảng phân loại quốc tế mã hóa bệnh tật, nguyên nhân tử vong ICD-10" và "Hướng dẫn mã hóa bệnh tật theo ICD-10" tại cơ sở khám bệnh, chữa bệnh" do Bộ trưởng Bộ Y tế ban hành""")
CHUNK_ROW_SIZE = 30
def chunk_rows(rows: list, chunk_size: int):
    for i in range(0, len(rows), chunk_size):
        yield rows[i:i + chunk_size]

# ==========================================================
# COMMON UTILITIES
# ==========================================================
def extract_annex_title(df, max_rows=6):
    """
    Extract annex title from top rows (merged cells).
    """
    lines = []
    for i in range(min(len(df), max_rows)):
        row = df.iloc[i]
        values = [str(c).strip() for c in row if pd.notna(c)]
        if values:
            lines.append(" ".join(values))

    text = " ".join(lines)
    text = " ".join(text.split())
    return text if len(text) > 20 else None


def detect_header_row(df, scan_rows=20):
    """
    Robust header detection for legal Excel:
    1. Any cell CONTAINS 'STT'
    2. Fallback: row index 1 or 2
    3. Fallback: dense string row
    """

    # -------- TIER 1: regex STT --------
    for i in range(min(len(df), scan_rows)):
        row = df.iloc[i]
        for cell in row:
            if pd.notna(cell):
                text = str(cell).strip().upper()
                if "STT" in text:
                    return i

    # -------- TIER 2: fixed position --------
    for i in (1, 2):
        if i < len(df):
            row = df.iloc[i]
            non_empty = sum(
                1 for c in row
                if pd.notna(c) and str(c).strip() != ""
            )
            if non_empty >= 3:
                return i

    # -------- TIER 3: density heuristic --------
    for i in range(min(len(df), scan_rows)):
        row = df.iloc[i]
        texts = [
            c for c in row
            if pd.notna(c)
            and isinstance(c, str)
            and len(c.strip()) > 1
        ]
        if len(texts) >= 3:
            return i

    return None

def normalize_cell_text(value):
    """
    Chuẩn hóa nội dung trong 1 ô Excel:
    - Gộp các dòng trong ô thành 1 dòng
    - Xóa khoảng trắng thừa
    """
    if pd.isna(value):
        return ""

    text = str(value)

    # thay xuống dòng, tab bằng khoảng trắng
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")

    # gom nhiều space thành 1
    text = " ".join(text.split())

    return text

# ==========================================================
# PDF HANDLER
# ==========================================================
def extract_tables_from_pdf(pdf_path: str, output_path: str = None,
                            start_page: int = 1, end_page: int = None):

    if not os.path.exists(pdf_path):
        print(f"❌ PDF not found: {pdf_path}")
        return

    if output_path is None:
        output_path = os.path.splitext(pdf_path)[0] + "_tables.md"

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        start_idx = start_page - 1
        end_idx = end_page if end_page else total_pages

        print(f"📄 PDF: {pdf_path}")
        print(f"📝 Output: {output_path}")
        print(f"📊 Pages {start_page} → {end_idx}")
        print("=" * 60)

        mode = 'a' if start_page > 1 else 'w'

        with open(output_path, mode, encoding="utf-8") as f:
            # if mode == 'w':
            #     f.write(f"# Tables extracted from {os.path.basename(pdf_path)}\n\n")

            table_count = 0
            for page_num in range(start_idx, end_idx):
                page = pdf.pages[page_num]
                tables = page.extract_tables()

                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    f.write(f"\n<!-- Page {page_num + 1} -->\n")

                    header = table[0]
                    f.write("| " + " | ".join(str(c or "").replace("\n", " ") for c in header) + " |\n")
                    f.write("|" + "|".join(["---"] * len(header)) + "|\n")

                    for row in table[1:]:
                        f.write("| " + " | ".join(str(c or "").replace("\n", " ") for c in row) + " |\n")

                    table_count += 1

        print(f"✅ Done! Extracted {table_count} tables")
        print(f"📝 Saved to: {output_path}")


# ==========================================================
# EXCEL HANDLER (LEGAL ANNEX AWARE)
# ==========================================================
def extract_tables_from_excel_folder(input_folder: str, output_folder: str = None):
    input_path = Path(input_folder)

    if not input_path.exists():
        print(f"❌ Folder not found: {input_folder}")
        return

    output_folder = Path(output_folder) if output_folder else input_path / "output_txt"
    output_folder.mkdir(parents=True, exist_ok=True)

    excel_files = list(input_path.glob("*.xls")) + list(input_path.glob("*.xlsx"))

    print(f"📂 Excel folder: {input_folder}")
    print(f"📝 Output folder: {output_folder}")
    print(f"📊 Found {len(excel_files)} Excel files")
    print("=" * 60)

    for excel_file in excel_files:
        print(f"📄 Processing: {excel_file.name}")
        out_file = output_folder / f"{excel_file.stem}.txt"

        try:
            sheets_raw = pd.read_excel(
                excel_file,
                sheet_name=None,
                header=None
            )
        except Exception as e:
            print(f"❌ Failed to read {excel_file.name}: {e}")
            continue

        with open(out_file, "w", encoding="utf-8") as f:
            # f.write(f"# Tables extracted from {excel_file.name}\n\n")

            for sheet_name, raw_df in sheets_raw.items():
                if raw_df.empty:
                    continue

                # 1. Annex title
                annex_title = extract_annex_title(raw_df)

                # 2. Detect header
                header_row = detect_header_row(raw_df)
                if header_row is None:
                    print(f"⚠️ No header detected: {excel_file.name} / {sheet_name}")
                    continue

                # 3. Build dataframe
                df = raw_df.iloc[header_row + 1:].copy()
                df.columns = raw_df.iloc[header_row].astype(str).str.strip()
                df = df.reset_index(drop=True)

                # drop empty / unnamed columns
                df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
                df = df.map(normalize_cell_text)
                if df.empty:
                    continue
                # 4. Write chunks (CHUẨN LLM)
                headers = df.columns.tolist()
                rows = df.values.tolist()

                row_chunks = list(chunk_rows(rows, CHUNK_ROW_SIZE))

                for idx, chunk in enumerate(row_chunks):
                    # ===== 1. TITLE CHUNG CỦA VĂN BẢN =====
                    f.write(DOCUMENT_TITLE.strip() + "\n")
                    # ===== 2. TÊN PHỤ LỤC =====
                    if annex_title:
                        f.write(annex_title.strip() + "\n")
                    # ===== 3. HEADER =====
                    f.write("| " + " | ".join(headers) + " |\n")
                    f.write("|" + "|".join(["---"] * len(headers)) + "|\n")
                    # ===== 4. NỘI DUNG =====
                    for row in chunk:
                        f.write("| " + " | ".join(map(str, row)) + " |\n")
                    # ===== 5. DÒNG TRỐNG GIỮA CÁC CHUNK =====
                    if not (
                        sheet_name == list(sheets_raw.keys())[-1]
                        and idx == len(row_chunks) - 1
                    ):
                        f.write("\n")
        print(f"✅ Saved: {out_file}")

    print("\n🎉 Done extracting Excel tables!")


# ==========================================================
# CLI
# ==========================================================
def main():
    parser = argparse.ArgumentParser(
        description="Extract tables from PDF or Excel to Markdown (legal annex aware)"
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    # PDF
    pdf_parser = subparsers.add_parser("pdf", help="Extract tables from PDF")
    pdf_parser.add_argument("pdf_path")
    pdf_parser.add_argument("-o", "--output")
    pdf_parser.add_argument("-s", "--start", type=int, default=1)
    pdf_parser.add_argument("-e", "--end", type=int)

    # EXCEL
    excel_parser = subparsers.add_parser("excel", help="Extract tables from Excel folder")
    excel_parser.add_argument("folder", help="Folder containing xls/xlsx files")
    excel_parser.add_argument("-o", "--output", help="Output folder")

    args = parser.parse_args()

    if args.mode == "pdf":
        extract_tables_from_pdf(
            args.pdf_path, args.output, args.start, args.end
        )
    elif args.mode == "excel":
        extract_tables_from_excel_folder(
            args.folder, args.output
        )


if __name__ == "__main__":
    main()