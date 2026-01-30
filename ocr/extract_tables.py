#!/usr/bin/env python3
"""
Extract tables from PDF to Markdown using pdfplumber.
No AI model required - fast and free!

Supports:
- Tables with borders (default)
- Borderless tables (--text-strategy)
"""

import argparse
import os

import pdfplumber


def extract_tables(pdf_path: str, output_path: str = None, start_page: int = 1, 
                   end_page: int = None, text_strategy: bool = False):
    """
    Extract tables from PDF and save as markdown.
    
    Args:
        pdf_path: Path to PDF file
        output_path: Output markdown file (default: same name as PDF with _tables.md)
        start_page: Start page (1-indexed, default: 1)
        end_page: End page (inclusive, default: last page)
        text_strategy: Use text-based table detection for borderless tables
    """
    if not os.path.exists(pdf_path):
        print(f"❌ PDF not found: {pdf_path}")
        return
    
    if output_path is None:
        output_path = os.path.splitext(pdf_path)[0] + "_tables.md"
    
    # Table settings
    table_settings = {}
    if text_strategy:
        table_settings = {
            'vertical_strategy': 'text',
            'horizontal_strategy': 'text',
            'snap_tolerance': 3,
            'join_tolerance': 3,
        }
    
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        start_idx = start_page - 1  # Convert to 0-indexed
        end_idx = end_page if end_page else total_pages
        
        print(f"📄 PDF: {pdf_path}")
        print(f"📝 Output: {output_path}")
        print(f"📊 Processing pages {start_page} to {end_idx} (total: {total_pages})")
        if text_strategy:
            print(f"🔤 Using text-based table detection (for borderless tables)")
        print("=" * 60)
        
        mode = 'a' if start_page > 1 else 'w'
        
        with open(output_path, mode, encoding='utf-8') as f:
            if mode == 'w':
                f.write(f"# Tables extracted from {os.path.basename(pdf_path)}\n\n")
            
            table_count = 0
            for page_num in range(start_idx, end_idx):
                page = pdf.pages[page_num]
                
                # Auto-detect: if no lines, try text strategy
                if not text_strategy and len(page.lines) == 0:
                    tables = page.extract_tables(table_settings={
                        'vertical_strategy': 'text',
                        'horizontal_strategy': 'text'
                    })
                else:
                    tables = page.extract_tables(table_settings=table_settings)
                
                if tables:
                    for table in tables:
                        if table and len(table) > 1:
                            f.write(f"\n<!-- Page {page_num + 1} -->\n")
                            
                            header = table[0]
                            if header:
                                # Filter out empty columns
                                clean_row = [str(c).replace("\n", " ").strip() if c else "" for c in header]
                                f.write("| " + " | ".join(clean_row) + " |\n")
                                f.write("|" + "|".join(["---"] * len(header)) + "|\n")
                            
                            for row in table[1:]:
                                if row:
                                    clean_row = [str(c).replace("\n", " ").strip() if c else "" for c in row]
                                    f.write("| " + " | ".join(clean_row) + " |\n")
                            
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
    parser.add_argument("-t", "--text-strategy", action="store_true",
                        help="Use text-based detection for borderless tables")
    
    args = parser.parse_args()
    extract_tables(args.pdf_path, args.output, args.start, args.end, args.text_strategy)


if __name__ == "__main__":
    main()

