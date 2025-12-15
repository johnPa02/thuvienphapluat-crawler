#!/usr/bin/env python3
"""
Pipeline hoàn chỉnh để crawl và xử lý văn bản pháp luật từ thuvienphapluat.vn

Sử dụng:
    python pipeline.py <url> [--output FILE] [--cookies FILE] [--doc-name NAME]

Ví dụ:
    python pipeline.py "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-47-2021-ND-CP-huong-dan-Luat-Doanh-nghiep-470561.aspx"
    python pipeline.py "https://thuvienphapluat.vn/van-ban/..." --output "luat_abc.txt" --doc-name "Luật ABC 2024"
"""

import argparse
import os
import re
import sys

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


def load_cookies_from_file(cookie_file: str) -> list:
    """
    Load cookies từ file Netscape format (cookies.txt).
    
    Args:
        cookie_file: Đường dẫn đến file cookies.txt
        
    Returns:
        List các cookie dict cho Playwright
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
    
    Args:
        url: URL của văn bản
        
    Returns:
        Tên văn bản (ví dụ: "Nghị định 47/2021/NĐ-CP")
    """
    # Pattern để tìm số hiệu văn bản trong URL
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
    
    Args:
        url: URL của trang web
        cookie_file: Đường dẫn đến file cookies.txt (optional)
        
    Returns:
        HTML content
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
    Ví dụ: id="span-note_khoan_34_4" -> tìm div id="note_khoan_34_4"
    """
    element_id = element.get('id', '')
    
    # Lấy note_id từ span-note_xxx -> note_xxx
    if element_id.startswith('span-'):
        note_id = element_id[5:]  # Bỏ "span-"
    else:
        return ""
    
    # Tìm div với id tương ứng trong dvNoteDieuKhoan
    note_div = soup.find('div', id=note_id)
    if note_div:
        note_text = note_div.get_text(separator=' ', strip=True)
        if note_text:
            # Tách lấy phần giải thích (sau |~|)
            parts = note_text.split('|~|')
            if len(parts) >= 2:
                # Phần đầu là nội dung bổ sung (không có [])
                # Phần sau là ghi chú nguồn (có [])
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
    # Xử lý các element có atmm hoặc onmouseover với lqhlTootip
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
            # Thay thế text "Bổ sung" bằng nội dung đầy đủ
            element.string = note_content


def extract_content(html: str) -> str:
    """
    Trích xuất nội dung text từ HTML.
    
    Args:
        html: HTML content
        
    Returns:
        Text content đã được chuẩn hóa
    """
    print("📄 Đang trích xuất nội dung...")
    
    soup = BeautifulSoup(html, "html.parser")
    content_div = soup.find("div", class_="content1")
    
    if content_div is None:
        raise ValueError("Không tìm thấy thẻ <div class='content1'> trên trang")
    
    # Xử lý hover tooltips
    process_element_with_hover(soup, content_div)
    
    # Xử lý các thẻ <b> chứa "Điều X." để tách tên điều và nội dung
    # 1. Normalize tên điều (bỏ newline trong thẻ <b>)
    # 2. Thêm marker sau thẻ <b> để xuống dòng
    DIEU_MARKER = "<<<DIEU_NEWLINE>>>"
    for b_tag in content_div.find_all('b'):
        text_content = b_tag.get_text()
        if re.match(r'^Điều\s+\d+\.', text_content):
            # Normalize: thay newline bằng space trong tên điều
            normalized_text = ' '.join(text_content.split())
            b_tag.string = normalized_text
            # Thêm marker sau thẻ <b> này
            from bs4 import NavigableString
            b_tag.insert_after(NavigableString(DIEU_MARKER))
    
    # Lấy text
    text = content_div.get_text()
    
    # Thay marker bằng newline
    text = text.replace(DIEU_MARKER, '\n')
    
    # Chuẩn hóa dòng
    lines = text.split('\n')
    result = []
    buffer = ""
    
    new_paragraph_patterns = [
        r'^Chương\s+[IVXLCDM]+',
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
    ]
    
    # Pattern để detect buffer kết thúc bằng tên Điều (cần xuống dòng sau đó)
    dieu_title_end_pattern = r'Điều\s+\d+\.\s+[^\n]+$'
    
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
            if buffer:
                # Kiểm tra nếu buffer là tên Điều (kết thúc bằng Điều X. Tên điều)
                # thì xuống dòng thay vì nối
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
    
    return '\n'.join(result)


def postprocess(content: str, doc_name: str) -> str:
    """
    Postprocess văn bản pháp luật.
    
    Args:
        content: Nội dung text thô
        doc_name: Tên văn bản pháp luật
        
    Returns:
        Nội dung đã được format
    """
    print("✨ Đang postprocess...")
    
    # Bỏ dấu chấm đứng một mình
    content = re.sub(r'\n\.\n', '\n', content)
    
    # Bỏ "[Click vào để xem nội dung]"
    content = content.replace(' [Click vào để xem nội dung]', '')
    content = content.replace('[Click vào để xem nội dung]', '')
    
    # Tách số khoản ra dòng mới khi bị dính vào ]
    content = re.sub(r'\]\s+(\d+\.)\s*\n', r']\n\1\n', content)
    content = re.sub(r'\]\s+(\d+\.)\s+', r']\n\1 ', content)
    
    # Thêm dòng trống và tên văn bản trước Chương
    content = re.sub(r'(Chương\s+[IVXLCDM]+)', rf'\n{doc_name}. \1', content)
    
    # Thêm dòng trống và tên văn bản trước Mục
    content = re.sub(r'(Mục\s+\d+\.)', rf'\n{doc_name}. \1', content)
    
    # Thêm dòng trống và tên văn bản trước I. II. III. ...
    content = re.sub(r'\n((?:I|II|III|IV|V|VI|VII|VIII|IX|X)\.\s+[A-Z])', rf'\n\n{doc_name}. \1', content)
    
    # Nối dấu ngoặc kép đứng một mình vào dòng sau (trường hợp bị xuống dòng trong HTML)
    # Hỗ trợ cả " thường và "" Unicode (U+201C và U+201D)
    content = re.sub(r'[""\u201c\u201d]\s*\n+\s*(Điều)', r'"\1', content)
    
    # Thêm xuống dòng và tên văn bản trước mỗi Điều
    # Chỉ xử lý "Điều X." khi nó là tiêu đề (theo sau là tên điều - ít nhất 2 từ)
    # Không xử lý khi:
    # - "Điều X." ở cuối câu hoặc đứng một mình
    # - "Điều X." nằm trong ngoặc kép (trích dẫn)
    # Pattern: ký tự không phải newline và không phải dấu ngoặc kép (cả ASCII " và Unicode "" ) + "Điều X." + space + chữ cái + tên điều
    content = re.sub(r'([^\n""\u201c\u201d])(Điều\s+\d+\.[ \t]+[A-ZĐÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ][a-zđàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+)', rf'\1\n\n{doc_name}. \2', content)
    # Thêm doc_name cho Điều đã ở đầu dòng (chưa có doc_name) và theo sau là tên điều, không bắt đầu bằng ngoặc kép
    content = re.sub(r'^(Điều\s+\d+\.[ \t]+[A-ZĐÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ][a-zđàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+)', rf'{doc_name}. \1', content, flags=re.MULTILINE)
    # Thêm doc_name cho Điều X. nằm riêng một dòng (tên điều ở dòng tiếp theo bắt đầu bằng chữ hoa)
    content = re.sub(r'^(Điều\s+\d+\.)\n([A-ZĐÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ])', rf'{doc_name}. \1 \2', content, flags=re.MULTILINE)
    # Loại bỏ doc_name nếu dòng bắt đầu bằng ngoặc kép + Điều (trích dẫn) - hỗ trợ cả ASCII " và Unicode ""
    content = re.sub(r'["\u201c\u201d]' + re.escape(doc_name) + r'\. (Điều)', r'"\1', content)
    # Thêm dòng trống trước các dòng bắt đầu bằng doc_name. Điều (đảm bảo có 1 dòng trống)
    content = re.sub(r'\n(' + re.escape(doc_name) + r'\. Điều)', r'\n\n\1', content)
    
    # Loại bỏ dòng trống thừa (nhiều hơn 2 newline liên tiếp)
    content = re.sub(r'\n{3,}', r'\n\n', content)
    
    # Loại bỏ dòng trống thừa ở đầu file
    content = content.lstrip('\n')
    
    return content


def run_pipeline(url: str, cookie_file: str = "cookies.txt", doc_name: str = None) -> str:
    """
    Chạy pipeline hoàn chỉnh.
    
    Args:
        url: URL của văn bản pháp luật
        output_file: File output (optional)
        cookie_file: File cookies (default: cookies.txt)
        doc_name: Tên văn bản (auto-detect nếu không cung cấp)
        
    Returns:
        Nội dung văn bản đã xử lý
    """
    print("=" * 60)
    print("🚀 THUVIENPHAPLUAT CRAWLER PIPELINE")
    print("=" * 60)
    
    # Auto-detect doc name
    if not doc_name:
        doc_name = extract_doc_name_from_url(url)
    print(f"📋 Văn bản: {doc_name}")
    
    # Step 1: Crawl HTML
    html = crawl_html(url, cookie_file if os.path.exists(cookie_file) else None)
    print(f"   ✓ Đã tải {len(html):,} bytes HTML")
    
    # Step 2: Extract content
    content = extract_content(html)
    print(f"   ✓ Đã trích xuất {len(content):,} ký tự")
    
    # Step 3: Postprocess
    processed = postprocess(content, doc_name)
    print(f"   ✓ Đã postprocess xong")
    
    # Step 4: Thêm doc_name vào đầu file
    processed = f"{doc_name}\n{processed}"
    
    # Step 5: Save output
    output_file = f"{doc_name.replace(' ', '_').replace('/','-')}.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(processed)
    print(f"   ✓ Đã lưu vào: {output_file}")
    
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
    
    args = parser.parse_args()
    
    try:
        run_pipeline(
            url=args.url,
            cookie_file=args.cookies,
            doc_name=args.doc_name
        )
    except Exception as e:
        print(f"❌ Lỗi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
