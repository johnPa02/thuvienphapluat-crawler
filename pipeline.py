#!/usr/bin/env python3
"""
Pipeline hoàn chỉnh để crawl và xử lý văn bản pháp luật từ thuvienphapluat.vn
Sử dụng:
python pipeline.py <url> [--output FILE] [--cookies FILE] [--doc-name NAME]
Ví dụ:
python pipeline.py "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-47-2021-ND-CP-huong-dan-Luat-Doanh-nghiep-470561.aspx"
python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --output "luat_abc.txt" --doc-name "Luật ABC 2024"
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import argparse
import json
import os
import re
import sys
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from table_converter import is_data_table, process_tables_in_content
from urllib.parse import quote, urljoin

def load_cookies_from_file(cookie_file: str) -> list:
    """
    Load cookies từ file Netscape format (cookies.txt).
    """
    cookies = []
    with open(cookie_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                domain = parts[0]
                if domain.startswith('.'):
                    domain = domain[1:]
                cookie = {
                    'name': parts[5],
                    'value': parts[6],
                    'domain': domain,
                    'path': parts[2],
                    'secure': parts[3].upper() == 'TRUE',
                    'httpOnly': False,
                }
                try:
                    expires = int(parts[4])
                    if expires > 0:
                        cookie['expires'] = expires
                except:
                    pass
                cookies.append(cookie)
    return cookies

def extract_doc_name_from_url(url: str) -> str:
    """
    Tự động trích xuất tên văn bản từ URL.
    """
    patterns = [
        r'Nghi-dinh-(\d+)-(\d+)-ND-CP',
        r'Luat-(\d+)-(\d+)-QH(\d+)',
        r'Thong-tu-(\d+)-(\d+)-TT-([A-Z]+)',
        r'Quyet-dinh-(\d+)-(\d+)-QD-([A-Z]+)',
        r'Nghi-quyet-(\d+)-(\d+)-NQ-([A-Z]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            if 'Nghi-dinh' in url:
                return f"Nghị định {match.group(1)}/{match.group(2)}/NĐ-CP"
            elif 'Luat' in url:
                return f"Luật {match.group(1)}/{match.group(2)}/QH{match.group(3)}"
            elif 'Thong-tu' in url:
                return f"Thông tư {match.group(1)}/{match.group(2)}/TT-{match.group(3)}"
            elif 'Quyet-dinh' in url:
                return f"Quyết định {match.group(1)}/{match.group(2)}/QĐ-{match.group(3)}"
            elif 'Nghi-quyet' in url:
                return f"Nghị quyết {match.group(1)}/{match.group(2)}/NQ-{match.group(3)}"
    return "Văn bản"

def crawl_html(url: str, cookie_file: str = None) -> str:
    """
    Crawl HTML từ URL với JavaScript rendering.
    """
    print(f"🌐 Đang crawl: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        if cookie_file and os.path.exists(cookie_file):
            cookies = load_cookies_from_file(cookie_file)
            context.add_cookies(cookies)
            print(f"🍪 Đã load {len(cookies)} cookies từ {cookie_file}")
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()
        return html

def extract_form_template_ids(content_div) -> list:
    """
    Extract tất cả form template IDs từ các element có class='clsBookmark_bm'
    Returns: List of tuples [(template_text, law_id, bookmark_id, element), ...]
    """
    form_templates = []
    
    # Tìm tất cả các element có class='clsBookmark_bm' và onclick chứa LS_Tip_Type_Bookmark_bm
    bookmark_elements = content_div.find_all(
        'a', 
        class_='clsBookmark_bm',
        onclick=re.compile(r'LS_Tip_Type_Bookmark_bm')
    )
    
    for element in bookmark_elements:
        onclick_attr = element.get('onclick', '')
        
        # Extract LawID và Bookmark_ID từ onclick
        # Pattern: LS_Tip_Type_Bookmark_bm('516302','4444790')
        id_match = re.search(r"LS_Tip_Type_Bookmark_bm\(['\"](\d+)['\"],\s*['\"](\d+)['\"]\)", onclick_attr)
        
        if id_match:
            law_id = id_match.group(1)
            bookmark_id = id_match.group(2)
            template_text = element.get_text(strip=True)
            
            form_templates.append((template_text, law_id, bookmark_id, element))
    
    return form_templates

# Thêm import random ở đầu file nếu chưa có
import random

def fetch_form_template_url(page, law_id: str, bookmark_id: str, retry_count: int = 0, max_retries: int = 3) -> str:
    """
    Gọi AJAX endpoint để lấy URL download của biểu mẫu.
    Returns: Full URL đã được encode, hoặc empty string nếu fail.
    """
    from urllib.parse import quote, urlparse, urlunparse
    
    try:
        # Call AJAX endpoint
        response = page.evaluate(f"""
            fetch('/page/ajaxcontroler.aspx', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded',
                }},
                body: 'action=LoadBieuMau&LawID={law_id}&Bookmark_ID={bookmark_id}'
            }}).then(r => r.text())
        """)
        
        if not response:
            return ""
        
        # Clean response - remove newlines, whitespace
        file_path = response.strip().split('\n')[0].strip()
        
        # 🚨 QUAN TRỌNG: Kiểm tra nếu response là HTML (Cloudflare challenge/error)
        if (file_path.startswith('<!DOCTYPE') or 
            file_path.startswith('<html') or 
            '<title>Just a moment' in file_path or 
            'Cloudflare' in file_path):
            
            print(f"      ⚠️  AJAX trả về Cloudflare challenge, retry sau 1-2s... (lần {retry_count + 1}/{max_retries})")
            if retry_count < max_retries:
                import time
                time.sleep(random.uniform(1.0, 2.0))  # Wait 1-2 seconds before retry
                return fetch_form_template_url(page, law_id, bookmark_id, retry_count + 1, max_retries)
            return ""
        
        # Kiểm tra nếu response là URL hợp lệ (chứa .doc, .pdf, .xls...)
        if not re.search(r'\.(doc|docx|pdf|xls|xlsx|ppt|pptx|zip|rar)($|\?)', file_path, re.I):
            print(f"      ⚠️  AJAX trả về path không phải file: {file_path[:100]}")
            return ""
        
        # ✅ FIX QUAN TRỌNG: Construct full URL
        if file_path.startswith('http'):
            # 🎯 Đã là full URL, CHỈ encode filename part, KHÔNG prepend base URL nữa
            parsed = urlparse(file_path)
            path_parts = parsed.path.rsplit('/', 1)
            if len(path_parts) == 2:
                base_path = path_parts[0]  # e.g., /uploads/DocForms/4/6/4/1/464109/
                filename = path_parts[1]    # e.g., Mẫu số 11/TXNK Phụ lục 1.doc
                encoded_filename = quote(filename, safe='')
                # Reconstruct URL with encoded filename only
                new_path = base_path + '/' + encoded_filename
                return urlunparse(parsed._replace(path=new_path))
            return file_path  # Return as-is if can't parse
        else:
            # Relative path, construct full URL
            if file_path.startswith('/'):
                file_path = file_path[1:]
            
            parts = file_path.rsplit('/', 1)
            if len(parts) == 2:
                base_path = parts[0]
                filename = parts[1]
                encoded_filename = quote(filename, safe='')
                full_url = f"https://files.thuvienphapluat.vn/{base_path}/{encoded_filename}"
            else:
                # Fallback: encode entire path
                full_url = "https://" + quote(file_path, safe=':/')
            
            return full_url
        
    except Exception as e:
        print(f"      ⚠️  Lỗi fetch form template URL: {e}")
        if retry_count < max_retries:
            import time
            time.sleep(random.uniform(1.0, 2.0))
            return fetch_form_template_url(page, law_id, bookmark_id, retry_count + 1, max_retries)
        return ""
def process_form_templates(page, content_div, base_url: str) -> dict:
    """
    Process all form templates và replace với markdown links.
    Returns: Dict mapping template text to download URL
    """
    print("📋 Đang xử lý biểu mẫu...")
    
    form_templates = extract_form_template_ids(content_div)
    template_urls = {}
    
    for template_text, law_id, bookmark_id, element in form_templates:
        print(f"   🔄 Fetching: {template_text}")
        
        # Try AJAX with retry logic (no fallback)
        download_url = fetch_form_template_url(page, law_id, bookmark_id, retry_count=0, max_retries=3)
        
        if download_url:
            template_urls[template_text] = download_url
            
            # Create markdown link
            markdown_link = f" [{template_text}]({download_url})"
            
            # Replace element with markdown link
            element.replace_with(markdown_link)
            print(f"   ✅ {template_text} -> {download_url[:80]}...")
        else:
            # Keep original text if fetch fails completely (no fallback)
            print(f"   ❌ Failed to fetch: {template_text}")
    
    return template_urls
def save_form_template_urls(template_urls: dict, output_file: str):
    """
    Save form template URLs to JSON file.
    """
    if template_urls:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(template_urls, f, ensure_ascii=False, indent=2)
        print(f"💾 Đã lưu {len(template_urls)} form template URLs vào: {output_file}")

def extract_hover_content(soup: BeautifulSoup, element) -> str:
    """
    Trích xuất nội dung hover tooltip từ element.
    """
    tooltip_class = None
    if element.get('atmm'):
        tooltip_class = element.get('atmm').strip('.')
    elif element.get('onmouseover'):
        match = re.search(r"['\"]\.([^'\"]+)['\"]", element.get('onmouseover'))
        if match:
            tooltip_class = match.group(1)
    
    if not tooltip_class:
        return ""
    
    tooltip_div = soup.find('div', class_=tooltip_class)
    if tooltip_div:
        tooltip_text = tooltip_div.get_text(separator=' ', strip=True)
        if tooltip_text and tooltip_text != "Click vào để xem nội dung":
            return f" [{tooltip_text}]"
    return ""

def extract_note_content(soup: BeautifulSoup, element) -> str:
    """
    Trích xuất nội dung từ dvNoteDieuKhoan dựa vào id của element.
    """
    element_id = element.get('id', '')
    if element_id.startswith('span-'):
        note_id = element_id[5:]  # Bỏ "span-"
    else:
        return ""
    
    note_div = soup.find('div', id=note_id)
    if note_div:
        note_text = note_div.get_text(separator=' ', strip=True)
        if note_text:
            parts = note_text.split('|~|')
            if len(parts) >= 2:
                main_content = parts[0].strip()
                source_note = parts[1].strip() if len(parts) > 1 else ""
                if source_note:
                    return f"\n{main_content} [{source_note}]"
                return f"\n{main_content}"
        return f"\n{note_text}"
    return ""

def process_element_with_hover(soup: BeautifulSoup, content_div) -> None:
    """
    Xử lý các element có hover và chèn nội dung tooltip vào sau text.
    """
    hover_elements = content_div.find_all(attrs={'atmm': True})
    hover_elements += content_div.find_all(attrs={'onmouseover': re.compile(r'lqhlTootip', re.I)})
    
    seen = set()
    unique_elements = []
    for el in hover_elements:
        if id(el) not in seen:
            seen.add(id(el))
            unique_elements.append(el)
    
    for element in unique_elements:
        hover_content = extract_hover_content(soup, element)
        if hover_content:
            element.append(hover_content)
    
    # Xử lý các element <huongdan> với id="span-note_..."
    huongdan_elements = content_div.find_all('huongdan', id=re.compile(r'^span-note_'))
    for element in huongdan_elements:
        note_content = extract_note_content(soup, element)
        if note_content:
            element.string = note_content

def extract_content(html: str, url: str = None, page=None) -> tuple:
    """
    Trích xuất nội dung text từ HTML.
    Returns: (text_content, form_template_urls_dict)
    """
    print("📄 Đang trích xuất nội dung...")
    
    soup = BeautifulSoup(html, "html.parser")
    content_div = soup.find("div", class_="content1")
    if content_div is None:
        raise ValueError("Không tìm thấy thẻ <div class='content1'> trên trang")
    
    # Xử lý hover tooltips
    process_element_with_hover(soup, content_div)
    
    # Xử lý form templates (BIỂU MẪU) - CẦN PAGE ĐỂ GỌI AJAX
    form_template_urls = {}
    if page:
        form_template_urls = process_form_templates(page, content_div, url)
    
    # Xử lý các thẻ <b> chứa "Điều X." để tách tên điều và nội dung
    DIEU_MARKER = "<<<DIEU_NEWLINE>>>"
    for b_tag in content_div.find_all('b'):
        text_content = b_tag.get_text()
        if re.match(r'^Điều\s+\d+\.', text_content):
            normalized_text = ' '.join(text_content.split())
            b_tag.string = normalized_text
            from bs4 import NavigableString
            b_tag.insert_after(NavigableString(DIEU_MARKER))
    
    # Xử lý các bảng
    print("🔄 Đang chuyển đổi bảng thành Markdown...")
    markdown_tables = []
    table_placeholders = []
    
    tables = content_div.find_all('table')
    for idx, table in enumerate(tables):
        if is_data_table(table):
            from table_converter import convert_table_to_markdown
            markdown = convert_table_to_markdown(table, base_url=url)
            markdown_tables.append(markdown)
            placeholder = f"<<<TABLE_{idx}>>>"
            table_placeholders.append(placeholder)
            placeholder_div = BeautifulSoup('<div></div>', 'html.parser').div
            placeholder_div.string = placeholder
            table.replace_with(placeholder_div)
    
    # Lấy text
    text = content_div.get_text()
    text = text.replace(DIEU_MARKER, '\n')
    
    # Chuẩn hóa dòng
    lines = text.split('\n')
    result = []
    buffer = ""
    
    new_paragraph_patterns = [
        r'^Mục\s+\d+',
        r'^Điều\s+\d+',
        r'^\d+\.\s',
        r'^[a-zđ]\)\s',
        r'^-\s',
        r'^PHỤ LỤC',
        r'^NGHỊ ĐỊNH',
        r'^Căn cứ',
        r'^Theo đề nghị',
        r'^Nơi nhận:',
        r'^TM\.',
        r'^CỘNG HÒA',
        r'^CHÍNH PHỦ',
        r'^Số:',
        r'^Hà Nội,',
        r'^Biểu số',
        r'^BẢNG',
        r'^TT$',
        r'^I\.\s',
        r'^II\.\s',
        r'^III\.\s',
        r'^IV\.\s',
        r'^V\.\s',
        r'^VI\.\s',
        r'^\s*\[\d+\]\s*'
    ]
    
    dieu_title_end_pattern = r'Điều\s+\d+\.\s+[^ ]+$'
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        is_new_paragraph = any(re.match(p, line) for p in new_paragraph_patterns)
        
        if is_new_paragraph:
            if buffer:
                result.append(buffer)
                buffer = line
            else:
                buffer = line
        else:
            if buffer:
                if re.search(dieu_title_end_pattern, buffer):
                    result.append(buffer)
                    buffer = line
                elif re.search(r'[.;:?!]$', buffer):
                    result.append(buffer)
                    buffer = line
                else:
                    buffer = buffer + " " + line
            else:
                buffer = line
    
    if buffer:
        result.append(buffer)
    
    final_text = '\n'.join(result)
    
    for placeholder, markdown in zip(table_placeholders, markdown_tables):
        markdown_with_spacing = f"\n{markdown.strip()}"
        final_text = final_text.replace(placeholder, markdown_with_spacing)
    
    return final_text, form_template_urls

def postprocess(content: str, doc_name: str) -> str:
    """
    Postprocess văn bản pháp luật.
    """
    print("✨ Đang postprocess...")
    
    content = re.sub(r'\n\.\n', '\n', content)
    content = content.replace(' [Click vào để xem nội dung]', '')
    content = content.replace('[Click vào để xem nội dung]', '')
    
    content = re.sub(r'(Mục\s+\d+\.)', rf'\n{doc_name}. \1', content)
    
    content = re.sub(r'[""\u201c\u201d]\s*\n+\s*(Điều)', r'"\1', content)
    
    pattern = r'([^"\n""\u201c\u201d])(?!(?:' + re.escape(doc_name) + r'\.\s*)?)(Điều\s+\d+\.[ \t]+[A-ZĐÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ][a-zđàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+)'
    replacement = rf'\1\n{doc_name}. \2'
    content = re.sub(pattern, replacement, content)
    
    content = re.sub(r'^(?!(?:' + re.escape(doc_name) + r'\.\s*)?)(Điều\s+\d+\.[ \t]+[A-ZĐÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸ][a-zđàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+)', rf'{doc_name}. \1', content, flags=re.MULTILINE)
    
    content = re.sub(r'["\u201c\u201d]' + re.escape(doc_name) + r'\. (Điều)', r'"\1', content)
    
    content = re.sub(r'\n(' + re.escape(doc_name) + r'\. Điều)', r'\n\1', content)
    
    content = re.sub(r'\n{3,}', r'\n', content)
    content = content.lstrip('\n')
    
    return content

def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to be safe for filesystem.
    """
    # Replace unsafe characters
    filename = filename.replace('/', '_').replace('\\', '_')
    filename = filename.replace(':', '_').replace('*', '_')
    filename = filename.replace('?', '_').replace('"', '_')
    filename = filename.replace('<', '_').replace('>', '_').replace('|', '_')
    return filename

def run_pipeline(url: str, cookie_file: str = "cookies.txt", doc_name: str = None, output_dir: str = ".") -> str:
    """
    Chạy pipeline hoàn chỉnh.
    """
    print("=" * 60)
    print("🚀 THUVIENPHAPLUAT CRAWLER PIPELINE")
    print("=" * 60)
    
    # Auto-detect doc name
    if not doc_name:
        doc_name = extract_doc_name_from_url(url)
    print(f"📋 Văn bản: {doc_name}")
    
    # Create output directory if needed
    if output_dir and output_dir != ".":
        os.makedirs(output_dir, exist_ok=True)
    
    print("🌐 Đang crawl...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        
        if cookie_file and os.path.exists(cookie_file):
            cookies = load_cookies_from_file(cookie_file)
            context.add_cookies(cookies)
            print(f"🍪 Đã load {len(cookies)} cookies từ {cookie_file}")
        
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        
        html = page.content()
        print(f"   ✓ Đã tải {len(html):,} bytes HTML")
        
        # Extract content với form templates (cần page object)
        content, form_template_urls = extract_content(html, url=url, page=page)
        print(f"   ✓ Đã trích xuất {len(content):,} ký tự")
        
        browser.close()
    
    # Postprocess
    processed = postprocess(content, doc_name)
    print(f"   ✓ Đã postprocess xong")
    
    # Thêm doc_name vào đầu file
    processed = f"{doc_name}\n{processed}"
    
    # Generate output filename
    safe_doc_name = sanitize_filename(doc_name)
    output_file = os.path.join(output_dir, f"{safe_doc_name}.txt") if output_dir else f"{safe_doc_name}.txt"
    
    # Save main content
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(processed)
    print(f"   ✓ Đã lưu vào: {output_file}")
    
    # Save form template URLs to JSON
    if form_template_urls:
        data_url_file = os.path.join(output_dir, f"{safe_doc_name}_data_url.json") if output_dir else f"{safe_doc_name}_data_url.json"
        save_form_template_urls(form_template_urls, data_url_file)
    
    print("=" * 60)
    print("✅ HOÀN THÀNH!")
    print("=" * 60)
    
    return processed

def main():
    parser = argparse.ArgumentParser(
        description="Crawl và xử lý văn bản pháp luật từ thuvienphapluat.vn",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
python pipeline.py "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-47-2021-ND-CP-huong-dan-Luat-Doanh-nghiep-470561.aspx"
python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --output "output.txt"
python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --doc-name "Luật ABC 2024"
"""
    )
    parser.add_argument("url", help="URL của văn bản pháp luật trên thuvienphapluat.vn")
    parser.add_argument("-c", "--cookies", default="cookies.txt", help="File cookies (default: cookies.txt)")
    parser.add_argument("-n", "--doc-name", help="Tên văn bản (auto-detect nếu không cung cấp)")
    parser.add_argument("-o", "--output-dir", default=".", help="Output directory (default: current directory)")
    args = parser.parse_args()
    
    try:
        run_pipeline(
            url=args.url,
            cookie_file=args.cookies,
            doc_name=args.doc_name,
            output_dir=args.output_dir
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Lỗi: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()