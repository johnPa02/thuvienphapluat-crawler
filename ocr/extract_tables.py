#!/usr/bin/env python3
"""
Extract tables from PDF to Markdown using pdfplumber.
No AI model required - fast and free!
"""

import argparse
import os

import pdfplumber


def extract_tables(pdf_path: str, output_path: str = None, start_page: int = 1, end_page: int = None):
    """
    Extract tables from PDF and save as markdown.
    
    Args:
        pdf_path: Path to PDF file
        output_path: Output markdown file (default: same name as PDF with _tables.md)
        start_page: Start page (1-indexed, default: 1)
        end_page: End page (inclusive, default: last page)
    """
    if not os.path.exists(pdf_path):
        print(f"❌ PDF not found: {pdf_path}")
        return
    
    if output_path is None:
        output_path = os.path.splitext(pdf_path)[0] + "_tables.md"
    
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        start_idx = start_page - 1  # Convert to 0-indexed
        end_idx = end_page if end_page else total_pages
        
        print(f"📄 PDF: {pdf_path}")
        print(f"📝 Output: {output_path}")
        print(f"📊 Processing pages {start_page} to {end_idx} (total: {total_pages})")
        print("=" * 60)
        
        mode = 'a' if start_page > 1 else 'w'
        
        with open(output_path, mode, encoding='utf-8') as f:
            if mode == 'w':
                f.write(f"# Tables extracted from {os.path.basename(pdf_path)}\n\n")
            
            table_count = 0
            for page_num in range(start_idx, end_idx):
                page = pdf.pages[page_num]
                tables = page.extract_tables()
                
                if tables:
                    for table in tables:
                        if table and len(table) > 1:
                            f.write(f"\n<!-- Page {page_num + 1} -->\n")
                            
                            header = table[0]
                            if header:
                                f.write("| " + " | ".join(
                                    str(c).replace("\n", " ") if c else "" 
                                    for c in header
                                ) + " |\n")
                                f.write("|" + "|".join(["---"] * len(header)) + "|\n")
                            
                            for row in table[1:]:
                                if row:
                                    f.write("| " + " | ".join(
                                        str(c).replace("\n", " ") if c else "" 
                                        for c in row
                                    ) + " |\n")
                            
                            table_count += 1
                
                if (page_num + 1) % 100 == 0:
                    print(f"✅ Page {page_num + 1}/{end_idx}")
        
        print("\n" + "=" * 60)
        print(f"✅ Done! Extracted {table_count} table segments")
        print(f"📝 Saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract tables from PDF to Markdown (no AI required)"
    )
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("-o", "--output", help="Output markdown file path")
    parser.add_argument("-s", "--start", type=int, default=1, 
                        help="Start page (1-indexed, default: 1)")
    parser.add_argument("-e", "--end", type=int, 
                        help="End page (inclusive, default: last page)")
    
    args = parser.parse_args()
    extract_tables(args.pdf_path, args.output, args.start, args.end)


if __name__ == "__main__":
    main()
