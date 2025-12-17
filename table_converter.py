#!/usr/bin/env python3
"""
Module to convert HTML tables to Markdown format
"""

import re
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md


def is_data_table(table: Tag) -> bool:
    """
    Determine if a table contains structured data that should be converted to Markdown.

    Args:
        table: BeautifulSoup table element

    Returns:
        True if table should be converted to Markdown
    """
    # Check if table has visible borders
    style = table.get('style', '')
    border = table.get('border', '')

    # Tables with explicit borders are likely data tables
    if 'border' in style or border not in ['', '0']:
        return True

    # Check cells for borders
    cells = table.find_all(['td', 'th'])
    for cell in cells:
        cell_style = cell.get('style', '')
        if 'border' in cell_style:
            return True

    # Get all rows
    rows = table.find_all('tr')
    if len(rows) < 2:  # Need at least 2 rows for a proper table
        return False

    # Check structure
    col_counts = []
    has_header_like_content = False

    for row in rows:
        cols = row.find_all(['td', 'th'])
        if cols:  # Skip empty rows
            col_counts.append(len(cols))

            # Check if any cell contains header-like or list-like content
            for col in cols:
                text = col.get_text().strip()
                # Check for patterns that suggest this is a data table
                if re.match(r'^(Mẫu số|STT|TT|Số Thứ tự|Tên|Đơn vị tính|Chỉ tiêu)', text):
                    has_header_like_content = True

    # Convert to markdown if:
    # 1. Consistent column count AND has header-like content
    # 2. OR table has borders (already checked above)
    if col_counts and len(set(col_counts)) == 1 and max(col_counts) >= 2:
        # Has consistent structure
        if has_header_like_content:
            return True

        # Additional check: if many rows with same structure (likely a list/table)
        if len(col_counts) >= 5:
            return True

    return False


def extract_text_from_cell(cell: Tag, base_url: str = None) -> str:
    """
    Extract and clean text from a table cell, preserving links.

    Args:
        cell: BeautifulSoup td or th element
        base_url: Base URL for resolving relative links

    Returns:
        Cleaned text content with links preserved
    """
    # Remove script and style elements
    for script in cell(["script", "style"]):
        script.decompose()

    # Check for links in the cell
    links = cell.find_all('a')

    if links:
        # If cell contains links, extract them
        result_parts = []

        for link in links:
            # Get link text
            link_text = link.get_text(strip=True)

            # Check various attributes for the link
            href = link.get('href', '')
            onclick = link.get('onclick', '')
            name = link.get('name', '')

            # Build link information
            link_info = []

            # Add text first
            if link_text:
                link_info.append(link_text)

            # Add href if available
            if href:
                # For relative URLs, add base_url if provided
                if base_url and href.startswith('#'):
                    # Anchor link - just note it
                    link_info.append(f"[Link nội bộ: {href}]")
                elif href.startswith('http'):
                    link_info.append(f"[Link: {href}]")
                else:
                    link_info.append(f"[Link: {href}]")
                # Note: In a real implementation, you might want to resolve relative URLs
                # to absolute using urllib.parse.urljoin(base_url, href)

            # Add onclick info (for JavaScript links)
            if onclick:
                # Extract function name if it's a function call
                match = re.search(r'(\w+)\s*\(', onclick)
                if match:
                    link_info.append(f"[JavaScript: {match.group(1)}]")
                else:
                    link_info.append("[JavaScript link]")

            # Add name/anchor info
            if name:
                link_info.append(f"[ID: {name}]")

            # Combine all info
            result_parts.append(' '.join(link_info))

        # Get any text not in links
        # Temporarily remove links to get remaining text
        for link in links:
            link.decompose()

        remaining_text = cell.get_text(separator=' ', strip=True)
        if remaining_text and remaining_text not in ' '.join(result_parts):
            # Clean up and add remaining text
            remaining_text = remaining_text.replace('□', '☐')
            result_parts.append(remaining_text)

        text = ' '.join(result_parts)
    else:
        # No links, just get regular text
        text = cell.get_text(separator=' ', strip=True)

    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)

    # Handle special cases
    text = text.replace('□', '☐')  # Use empty checkbox
    text = text.replace('[Click vào để xem nội dung]', '')

    return text


def convert_table_to_markdown(table: Tag, base_url: str = None) -> str:
    """
    Convert HTML table to Markdown format.

    Args:
        table: BeautifulSoup table element
        base_url: Base URL for resolving relative links

    Returns:
        Markdown formatted table
    """
    rows = table.find_all('tr')
    if not rows:
        return ""

    markdown_rows = []
    headers_processed = False

    for i, row in enumerate(rows):
        cells = row.find_all(['td', 'th'])
        if not cells:
            continue

        # Extract text from cells
        cell_texts = []
        for cell in cells:
            text = extract_text_from_cell(cell, base_url=base_url)

            # Handle colspan - repeat text for missing columns
            colspan = int(cell.get('colspan', 1))
            cell_texts.extend([text] + [''] * (colspan - 1))

        # Create markdown row
        markdown_row = '| ' + ' | '.join(cell_texts) + ' |'
        markdown_rows.append(markdown_row)

        # Add separator after first row (header)
        if i == 0 and not headers_processed:
            separator = '| ' + ' | '.join(['---'] * len(cell_texts)) + ' |'
            markdown_rows.append(separator)
            headers_processed = True

    # Join rows with newlines
    markdown_table = '\n'.join(markdown_rows)

    # Add surrounding newlines
    return f'\n\n{markdown_table}\n\n'


def process_tables_in_content(content_div: Tag) -> None:
    """
    Find and convert all data tables in content to Markdown.

    Args:
        content_div: BeautifulSoup element containing content
    """
    # Find all tables
    tables = content_div.find_all('table')

    for table in tables:
        if is_data_table(table):
            # Convert table to markdown
            markdown = convert_table_to_markdown(table)

            # Replace table with markdown
            # Create a new element for the markdown
            markdown_div = BeautifulSoup('<div></div>', 'html.parser').div
            markdown_div.string = markdown

            # Replace the table
            table.replace_with(markdown_div)


def convert_html_with_tables(html: str) -> str:
    """
    Process HTML and convert tables to Markdown while preserving other content.

    Args:
        html: Raw HTML string

    Returns:
        HTML with tables converted to Markdown format
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Find content div
    content_div = soup.find("div", class_="content1")
    if content_div:
        process_tables_in_content(content_div)

    return str(soup)