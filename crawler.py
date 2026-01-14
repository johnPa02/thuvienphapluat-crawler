import re
import os

import requests
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
                # Bỏ dấu . ở đầu domain nếu có
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
                # Thêm expiry nếu có
                try:
                    expires = int(parts[4])
                    if expires > 0:
                        cookie['expires'] = expires
                except:
                    pass
                cookies.append(cookie)
    return cookies


def get_html_with_js(url: str, cookie_file: str = None) -> str:
    """
    Lấy HTML sau khi JavaScript đã render bằng Playwright.
    Hỗ trợ đăng nhập bằng file cookies.
    
    Args:
        url: URL của trang web
        cookie_file: Đường dẫn đến file cookies.txt (Netscape format)
        
    Returns:
        HTML content sau khi JS render
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        
        # Load cookies nếu có
        if cookie_file and os.path.exists(cookie_file):
            cookies = load_cookies_from_file(cookie_file)
            context.add_cookies(cookies)
            print(f"Đã load {len(cookies)} cookies từ {cookie_file}")
        
        page = context.new_page()
        
        # Truy cập URL văn bản
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        
        html = page.content()
        browser.close()
    return html


def extract_hover_content(soup: BeautifulSoup, element) -> str:
    """
    Trích xuất nội dung hover tooltip từ element.
    
    Args:
        soup: BeautifulSoup object của toàn bộ trang
        element: Element có hover event
        
    Returns:
        Nội dung tooltip trong dấu [] hoặc chuỗi rỗng nếu không có
    """
    # Tìm class tooltip từ attribute atmm hoặc onmouseover
    tooltip_class = None
    
    if element.get('atmm'):
        tooltip_class = element.get('atmm').strip('.')
    elif element.get('onmouseover'):
        # Tìm pattern như LS_Tootip_Type_Bookmark('.lqhlTootip-3432232')
        match = re.search(r"['\"]\.([^'\"]+)['\"]", element.get('onmouseover'))
        if match:
            tooltip_class = match.group(1)
    
    if not tooltip_class:
        return ""
    
    # Tìm div có class tương ứng
    tooltip_div = soup.find('div', class_=tooltip_class)
    if tooltip_div:
        tooltip_text = tooltip_div.get_text(separator=' ', strip=True)
        if tooltip_text:
            return f" [{tooltip_text}]"
    
    return ""


def process_element_with_hover(soup: BeautifulSoup, content_div) -> None:
    """
    Xử lý các element có hover và chèn nội dung tooltip vào sau text.
    
    Args:
        soup: BeautifulSoup object của toàn bộ trang
        content_div: Div chứa nội dung chính
    """
    # Tìm tất cả các element có hover (có attribute atmm hoặc onmouseover chứa Tootip)
    hover_elements = content_div.find_all(attrs={'atmm': True})
    hover_elements += content_div.find_all(attrs={'onmouseover': re.compile(r'lqhlTootip', re.I)})
    
    # Loại bỏ duplicate
    seen = set()
    unique_elements = []
    for el in hover_elements:
        if id(el) not in seen:
            seen.add(id(el))
            unique_elements.append(el)
    
    for element in unique_elements:
        hover_content = extract_hover_content(soup, element)
        if hover_content:
            # Thêm nội dung hover vào cuối element
            element.append(hover_content)


def crawl_content(url: str, use_js: bool = True, cookie_file: str = None) -> str:
    """
    Crawl nội dung text từ thẻ <div class="content1"> trên trang thuvienphapluat.vn
    
    Args:
        url: URL của trang văn bản pháp luật
        use_js: Sử dụng Playwright để render JavaScript (cần thiết để lấy tooltip)
        cookie_file: Đường dẫn đến file cookies.txt (cần để lấy tooltip)
        
    Returns:
        Nội dung text của văn bản
    """
    if use_js:
        html = get_html_with_js(url, cookie_file)
        soup = BeautifulSoup(html, "html.parser")
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
    
    # Tìm thẻ div với class="content1"
    content_div = soup.find("div", class_="content1")
    
    if content_div is None:
        raise ValueError("Không tìm thấy thẻ <div class='content1'> trên trang")
    
    # Xử lý các element có hover tooltip
    process_element_with_hover(soup, content_div)
    
    # Lấy text thô
    text = content_div.get_text()
    
    # Chuẩn hóa: nối các dòng bị ngắt giữa chừng
    # Nếu dòng không kết thúc bằng dấu câu (. : ; ? !) và dòng tiếp không bắt đầu pattern đặc biệt
    # thì nối lại với nhau
    
    lines = text.split('\n')
    result = []
    buffer = ""
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Các pattern bắt đầu đoạn mới (không nối với dòng trước)
        new_paragraph_patterns = [
            r'^Chương\s+[IVXLCDM]+',  # Chương I, II, III...
            r'^Mục\s+\d+',             # Mục 1, 2, 3...
            r'^Điều\s+\d+',            # Điều 1, 2, 3...
            r'^\d+\.\s',               # 1. 2. 3. (đầu khoản)
            r'^[a-zđ]\)\s',            # a) b) c) (đầu điểm)
            r'^-\s',                   # - (gạch đầu dòng)
            r'^PHỤ LỤC',               # Phụ lục
            r'^NGHỊ ĐỊNH',             # Tiêu đề
            r'^Căn cứ',                # Căn cứ
            r'^Theo đề nghị',          # Theo đề nghị
            r'^Nơi nhận:',             # Nơi nhận
            r'^TM\.',                  # TM. CHÍNH PHỦ
            r'^CỘNG HÒA',              # Header
            r'^CHÍNH PHỦ',             # Header
            r'^Số:',                   # Số văn bản
            r'^Hà Nội,',               # Địa điểm
            r'^Biểu số',               # Biểu mẫu
            r'^BẢNG',                  # Bảng
            r'^TT$',                   # Cột TT trong bảng
            r'^I\.\s',                 # I. II. III.
            r'^II\.\s',
            r'^III\.\s',
            r'^IV\.\s',
            r'^V\.\s',
            r'^VI\.\s',
        ]
        
        is_new_paragraph = any(re.match(p, line) for p in new_paragraph_patterns)
        
        if is_new_paragraph:
            # Lưu buffer cũ và bắt đầu đoạn mới
            if buffer:
                result.append(buffer)
            buffer = line
        else:
            # Kiểm tra xem dòng trước có kết thúc bằng dấu câu không
            if buffer:
                # Nếu buffer kết thúc bằng dấu câu hoàn chỉnh, bắt đầu dòng mới
                if re.search(r'[.;:?!]$', buffer):
                    result.append(buffer)
                    buffer = line
                else:
                    # Nối với dòng trước (thêm space)
                    buffer = buffer + " " + line
            else:
                buffer = line
    
    # Thêm phần còn lại
    if buffer:
        result.append(buffer)
    
    return '\n'.join(result)


def main():
    url = "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-47-2021-ND-CP-huong-dan-Luat-Doanh-nghiep-470561.aspx"
    output_file = "output.txt"
    cookie_file = "cookies.txt"  # File cookies từ browser
    
    print(f"Đang crawl nội dung từ: {url}")
    if os.path.exists(cookie_file):
        print(f"Sử dụng cookies từ: {cookie_file}")
    print("-" * 80)
    
    try:
        # content = crawl_content(url, use_js=True, cookie_file=cookie_file if os.path.exists(cookie_file) else None)
        content = crawl_content(url, use_js=True)
        # Lưu ra file txt
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)
        
        print(f"Đã lưu nội dung vào file: {output_file}")
    except requests.RequestException as e:
        print(f"Lỗi khi tải trang: {e}")
    except ValueError as e:
        print(f"Lỗi: {e}")


if __name__ == "__main__":
    main()

