#!/usr/bin/env python3
"""
Multi-threaded batch crawler for thuvienphapluat.vn
Processes multiple URLs concurrently with queue management and progress tracking

Usage:
    python batch_crawler.py urls.txt [--threads 4] [--cookies FILE] [--delay 1] [--retry 3] [--resume]

Example:
    python batch_crawler.py urls.txt --threads 8 --cookies cookies.txt
    python batch_crawler.py urls.txt --resume  # Continue from where we left off
"""

import argparse
import concurrent.futures
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict


class BatchCrawler:
    """Multi-threaded batch crawler that calls pipeline.py via subprocess"""

    def __init__(self, max_workers: int = 4, cookie_file: str = "cookies.txt",
                 delay_range: Tuple[float, float] = (1.0, 3.0), max_retries: int = 3,
                 output_dir: str = "crawl"):
        self.max_workers = max_workers
        self.cookie_file = cookie_file
        self.delay_range = delay_range
        self.max_retries = max_retries
        self.output_dir = output_dir

        # Thread safety - initialize locks first
        self.stats_lock = threading.Lock()
        self.print_lock = threading.Lock()

        # Create output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)

        with self.print_lock:
            print(f"📁 Output directory: {self.output_dir}")

        # Thread-safe data structures
        self.url_queue = queue.Queue()
        self.completed_queue = queue.Queue()
        self.failed_queue = queue.Queue()

        # Statistics
        self.stats = {
            'total': 0,
            'completed': 0,
            'failed': 0,
            'skipped': 0,
            'start_time': None,
            'end_time': None
        }

        # Resume functionality
        self.completed_urls = set()
        self.failed_urls = set()

        # # Pipeline command
        self.pipeline_cmd = ["uv", "run", "python", "pipeline.py"]

    def extract_doc_name_from_url(self, url: str) -> str:
        """Extract document name from URL"""
        patterns = [
            # Văn bản hợp nhất
            r'Van-ban-hop-nhat-(\d+)-VBHN-VPQH-(\d+)',
            # Nghị định
            r'Nghi-dinh-(\d+)-(\d+)-ND-CP',
            # Luật
            r'Luat-(\d+)-(\d+)-QH(\d+)',
            # Thông tư
            r'Thong-tu-(\d+)-(\d+)-TT-([A-Z]+)',
            # Quyết định
            r'Quyet-dinh-(\d+)-(\d+)-QD-([A-Z]+)',
            # Nghị quyết
            r'Nghi-quyet-(\d+)-(\d+)-NQ-([A-Z]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                if 'Van-ban-hop-nhat' in url:
                    return f"Văn bản hợp nhất {match.group(1)}/VBHN-VPQH/{match.group(2)}"
                elif 'Nghi-dinh' in url:
                    return f"Nghị định {match.group(1)}/{match.group(2)}/NĐ-CP"
                elif 'Luat' in url:
                    return f"Luật {match.group(1)}/{match.group(2)}/QH{match.group(3)}"
                elif 'Thong-tu' in url:
                    return f"Thông tư {match.group(1)}/{match.group(2)}/TT-{match.group(3)}"
                elif 'Quyet-dinh' in url:
                    return f"Quyết định {match.group(1)}/{match.group(2)}/QĐ-{match.group(3)}"
                elif 'Nghi-quyet' in url:
                    return f"Nghị quyết {match.group(1)}/{match.group(2)}/NQ-{match.group(3)}"

        # Handle Văn bản sửa đổi, bổ sung, liên quan
        if 'sua-doi' in url:
            if 'Luat' in url:
                # Extract the base law name
                if 'Luat-Doanh-nghiep' in url:
                    return "Luật Doanh nghiệp sửa đổi 2025 số 76/2025/QH15"
                elif 'Luat-Dau-tu' in url:
                    return "Luật Đầu tư sửa đổi"
                elif 'Luat-ngan-sach-nha-nuoc' in url:
                    return "Luật Ngân sách nhà nước 2025 số 89/2025/QH15"
                elif 'Luat-sua-doi-Luat-Dau-tu-cong' in url:
                    return "Luật sửa đổi Luật Đầu tư công, Luật Đầu tư theo phương thức đối tác công tư"
                else:
                    # Fallback: try to extract from URL
                    base_match = re.search(r'Luat-([^/]+)', url, re.IGNORECASE)
                    if base_match:
                        base_name = base_match.group(1).replace('-', ' ')
                        return f"Luật {base_name} sửa đổi"
                    return "Luật sửa đổi"
            elif 'Nghi-dinh' in url:
                base_match = re.search(r'Nghi-dinh-(\d+)-(\d+)-ND-CP', url, re.IGNORECASE)
                if base_match:
                    return f"Nghị định {base_match.group(1)}/{base_match.group(2)}/NĐ-CP sửa đổi"
                return "Nghị định sửa đổi"
            elif 'Thong-tu' in url:
                base_match = re.search(r'Thong-tu-(\d+)-(\d+)-TT-([A-Z]+)', url, re.IGNORECASE)
                if base_match:
                    return f"Thông tư {base_match.group(1)}/{base_match.group(2)}/TT-{base_match.group(3)} sửa đổi"
                return "Thông tư sửa đổi"
            else:
                # Fallback for other document types
                return "Văn bản sửa đổi"

        # Handle Văn bản liên quan
        elif 'lien-quan' in url or 'cong-van' in url:
            if 'cong-van' in url:
                cong_van_match = re.search(r'cong-van-(\d+)-([A-Z]+)-([A-Z]+)-(\d+)', url, re.IGNORECASE)
                if cong_van_match:
                    doc_type = ""
                    if cong_van_match.group(2) == 'VPCP' and cong_van_match.group(3) == 'DMDN':
                        doc_type = "Văn bản liên quan đến quản lý doanh nghiệp"
                    elif cong_van_match.group(2) == 'BKHDT' and cong_van_match.group(3) == 'QLKTTW':
                        doc_type = "Văn bản liên quan đến quản lý kế toán"

                    return f"{doc_type} số {cong_van_match.group(1)}/{cong_van_match.group(4)}"
                return "Công văn liên quan"
            else:
                return "Văn bản liên quan"

        # Enhanced fallback: extract meaningful info from URL path
        url_parts = url.split('/')
        if len(url_parts) >= 2:
            # Get the last part (filename)
            filename = url_parts[-1]
            # Remove the .aspx extension and split
            clean_name = filename.replace('.aspx', '')

            # Try to extract document type and content
            if '-Luat-' in clean_name:
                parts = clean_name.split('-Luat-')
                if len(parts) >= 2:
                    doc_type = "Luật"
                    content = parts[1].split('-')[0:3]  # Take first few words
                    content = ' '.join(content)
                    return f"{doc_type} {content}"
            elif '-Nghi-dinh-' in clean_name:
                parts = clean_name.split('-Nghi-dinh-')
                if len(parts) >= 2:
                    doc_type = "Nghị định"
                    content = parts[1].split('-')[0:3]
                    content = ' '.join(content)
                    return f"{doc_type} {content}"
            else:
                # Generic fallback - take first few meaningful words
                words = clean_name.split('-')
                if len(words) >= 3:
                    return f"Văn bản {' '.join(words[0:3])}"
                elif len(words) >= 2:
                    return f"Văn bản {' '.join(words[:2])}"

        return "Văn bản"

    def run_pipeline_subprocess(self, url: str, doc_name: str, retry_count: int = 0) -> Tuple[bool, str]:
        """Run pipeline.py as subprocess for a single URL"""
        try:
            with self.print_lock:
                print(f"🚀 [{threading.current_thread().name}] Đang crawl: {doc_name}")

            # Build command - pipeline.py runs from original directory
            # but we tell it where to save files using --output parameter if pipeline supports it
            # or we handle file movement afterwards

            # Try to get absolute path to pipeline.py
            pipeline_path = os.path.join(os.getcwd(), "pipeline.py")
            cmd = ["uv", "run", "python", pipeline_path]
            cmd.append(url)
            cmd.append("--cookies")
            cmd.append(self.cookie_file)

            if doc_name and doc_name != "Văn bản":
                cmd.append("--doc-name")
                cmd.append(doc_name)

            # Run subprocess from original directory
            result = subprocess.run(
                cmd,
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
                encoding='utf-8',          # 👈 Thêm dòng này
                errors='replace',          # 👈 Thay ký tự lỗi bằng 
                timeout=300
            )

            if result.returncode == 0:
                # Extract filename from pipeline output
                output_lines = result.stdout.strip().split('\n')
                filename = None

                for line in output_lines:
                    if "Đã lưu vào:" in line:
                        # Extract filename from line like "   ✓ Đã lưu vào: Nghị_định_47-2021-NĐ-CP.txt"
                        filename = line.split("Đã lưu vào:")[-1].strip()
                        break

                if filename:
                    # Move file to output directory
                    source_path = os.path.join(os.getcwd(), filename)
                    dest_path = os.path.join(self.output_dir, filename)

                    try:
                        import shutil
                        if os.path.exists(source_path):
                            shutil.move(source_path, dest_path)
                            with self.print_lock:
                                print(f"   [{threading.current_thread().name}] ✅ Đã lưu: {filename}")
                            print(f"   [{threading.current_thread().name}] 📁 Đã chuyển đến: {self.output_dir}")
                            return True, filename
                        else:
                            # File might already be in output directory
                            if os.path.exists(dest_path):
                                with self.print_lock:
                                    print(f"   [{threading.current_thread().name}] ✅ Đã lưu: {filename}")
                                return True, filename
                            else:
                                with self.print_lock:
                                    print(f"   [{threading.current_thread().name}] ⚠️  Không tìm thấy file output")
                                return False, "Không tìm thấy file output"
                    except Exception as move_error:
                        with self.print_lock:
                            print(f"   [{threading.current_thread().name}] ❌ Lỗi di chuyển file: {move_error}")
                        return False, str(move_error)

                else:
                    # Generate expected filename if not found in output
                    expected_filename = f"{doc_name.replace(' ', '_').replace('/', '_')}.txt"

                    # Check if file exists in current directory or output directory
                    source_path = os.path.join(os.getcwd(), expected_filename)
                    dest_path = os.path.join(self.output_dir, expected_filename)

                    if os.path.exists(source_path):
                        try:
                            import shutil
                            shutil.move(source_path, dest_path)
                            with self.print_lock:
                                print(f"   [{threading.current_thread().name}] ✅ Đã lưu: {expected_filename}")
                                print(f"   [{threading.current_thread().name}] 📁 Đã chuyển đến: {self.output_dir}")
                            return True, expected_filename
                        except Exception as move_error:
                            with self.print_lock:
                                print(f"   [{threading.current_thread().name}] ❌ Lỗi di chuyển file: {move_error}")
                            return False, str(move_error)
                    elif os.path.exists(dest_path):
                        with self.print_lock:
                            print(f"   [{threading.current_thread().name}] ✅ Đã lưu: {expected_filename}")
                        return True, expected_filename
                    else:
                        with self.print_lock:
                            print(f"   [{threading.current_thread().name}] ⚠️  Pipeline thành công nhưng không tìm thấy file output")
                        return False, "Không tìm thấy file output"
            else:
                error_msg = result.stderr.strip() if result.stderr else "Pipeline thất bại"
                with self.print_lock:
                    print(f"   [{threading.current_thread().name}] ❌ Pipeline lỗi: {error_msg}")
                    if result.stdout:
                        print(f"   [{threading.current_thread().name}] stdout: {result.stdout[:200]}...")
                return False, error_msg

        except subprocess.TimeoutExpired:
            error_msg = "Pipeline timeout sau 5 phút"
            with self.print_lock:
                print(f"   [{threading.current_thread().name}] ⏰ {error_msg}")
            return False, error_msg

        except Exception as e:
            if retry_count < self.max_retries:
                delay = (retry_count + 1) * 2  # Exponential backoff
                with self.print_lock:
                    print(f"   [{threading.current_thread().name}] ⚠️  Lỗi: {e}")
                    print(f"   [{threading.current_thread().name}] 🔄 Thử lại sau {delay}s... (lần {retry_count + 1}/{self.max_retries})")

                time.sleep(delay)
                return self.run_pipeline_subprocess(url, doc_name, retry_count + 1)
            else:
                error_msg = f"Thất bại sau {self.max_retries} lần thử: {str(e)}"
                with self.print_lock:
                    print(f"   [{threading.current_thread().name}] ❌ {error_msg}")
                return False, error_msg

    def worker_thread(self, thread_id: int):
        """Worker thread function"""
        thread_name = f"Worker-{thread_id:02d}"
        threading.current_thread().name = thread_name

        while True:
            url_item = None
            processed = False
            skipped = False

            try:
                # Get URL from queue
                url_item = self.url_queue.get(timeout=1)

                if url_item is None:  # Poison pill
                    break

                url, doc_name = url_item

                # Check if already completed
                if url in self.completed_urls:
                    with self.print_lock:
                        print(f"   [{thread_name}] ⏭️  Bỏ qua (đã hoàn thành): {doc_name}")
                    with self.stats_lock:
                        self.stats['skipped'] += 1
                    skipped = True
                else:
                    # Run pipeline via subprocess
                    success, result = self.run_pipeline_subprocess(url, doc_name)

                    if success:
                        with self.stats_lock:
                            self.stats['completed'] += 1
                            self.completed_urls.add(url)
                        self.completed_queue.put((url, doc_name, result))
                    else:
                        with self.stats_lock:
                            self.stats['failed'] += 1
                            self.failed_urls.add(url)
                        self.failed_queue.put((url, doc_name, result))

                    processed = True

            except queue.Empty:
                continue
            except Exception as e:
                with self.print_lock:
                    print(f"   [{thread_name}] 🚨 Lỗi worker: {e}")
                with self.stats_lock:
                    self.stats['failed'] += 1
                processed = True  # Đánh dấu đã xử lý lỗi

            finally:
                if url_item is not None:
                    self.url_queue.task_done()

                    # 👇 ÁP DỤNG DELAY SAU MỌI XỬ LÝ (kể cả skip, lỗi, thành công)
                    delay = random.uniform(*self.delay_range)
                    time.sleep(delay)
    def load_resume_state(self, resume_file: str = "crawl_state.json"):
        """Load resume state from file"""
        if os.path.exists(resume_file):
            try:
                with open(resume_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.completed_urls = set(state.get('completed_urls', []))
                    self.failed_urls = set(state.get('failed_urls', []))

                    print(f"📂 Đã tải state: {len(self.completed_urls)} hoàn thành, {len(self.failed_urls)} thất bại")
                    return True
            except Exception as e:
                print(f"⚠️  Không thể tải state file: {e}")
        return False

    def save_resume_state(self, resume_file: str = "crawl_state.json"):
        """Save current state to file"""
        state = {
            'completed_urls': list(self.completed_urls),
            'failed_urls': list(self.failed_urls),
            'timestamp': datetime.now().isoformat(),
            'stats': self.stats,
            'output_dir': self.output_dir
        }

        try:
            with open(resume_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  Không thể lưu state file: {e}")

    def load_urls_from_file(self, url_file: str) -> List[Tuple[str, str]]:
        """Load URLs from file"""
        urls = []
        try:
            with open(url_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    raw = line.rstrip('\n')
                    line = raw.strip()
                    if not line or line.startswith('#'):
                        continue

                    # Support lines with optional doc-name after the URL
                    # e.g. "<url> Luật ngân sách 2025"
                    parts = line.split()
                    if parts and parts[0].startswith('http'):
                        url = parts[0]

                        # If there's additional text after the URL, treat it as the doc_name
                        doc_name = None
                        if len(parts) > 1:
                            # Preserve the original spacing for the doc_name portion
                            first_space = raw.find(' ')
                            if first_space != -1:
                                doc_name = raw[first_space + 1 :].strip()

                        # Fallback to extractor when doc_name not provided
                        if not doc_name:
                            doc_name = self.extract_doc_name_from_url(url)

                        urls.append((url, doc_name))
                    else:
                        print(f"⚠️  Dòng {line_num}: URL không hợp lệ - {line}")

            print(f"📋 Đã tải {len(urls)} URL từ {url_file}")
            return urls

        except FileNotFoundError:
            print(f"❌ Không tìm thấy file: {url_file}")
            return []
        except Exception as e:
            print(f"❌ Lỗi đọc file: {e}")
            return []

    def print_progress(self):
        """Print progress information"""
        while True:
            try:
                with self.stats_lock:
                    total = self.stats['total']
                    completed = self.stats['completed']
                    failed = self.stats['failed']
                    skipped = self.stats['skipped']

                    if total > 0:
                        progress = (completed + failed + skipped) / total * 100
                        print(f"\r📊 Tiến độ: {progress:.1f}% ({completed + failed + skipped}/{total}) "
                              f"✅ {completed} ❌ {failed} ⏭️ {skipped}", end='', flush=True)

                time.sleep(2)

            except KeyboardInterrupt:
                break
            except Exception:
                continue

    def run(self, url_file: str, resume: bool = False):
        """Run the batch crawler"""
        print("🚀 THUVIENPHAPLUAT BATCH CRAWLER")
        print("=" * 60)

        # Load URLs
        urls = self.load_urls_from_file(url_file)
        if not urls:
            return

        # Load resume state if requested
        if resume:
            self.load_resume_state()

        # Filter out already completed URLs
        new_urls = [(url, doc_name) for url, doc_name in urls
                   if url not in self.completed_urls]

        print(f"📝 Cần crawl: {len(new_urls)} URL")
        print(f"⏭️  Bỏ qua: {len(urls) - len(new_urls)} URL (đã hoàn thành)")

        if not new_urls:
            print("✅ Tất cả URL đã được crawl!")
            return

        # Update statistics
        with self.stats_lock:
            self.stats['total'] = len(urls)
            self.stats['start_time'] = time.time()

        # Add URLs to queue
        for url_item in new_urls:
            self.url_queue.put(url_item)

        print(f"🔧 Bắt đầu crawl với {self.max_workers} threads...")
        print(f"⏱️  Delay: {self.delay_range[0]}-{self.delay_range[1]}s")
        print(f"🔄 Retry: {self.max_retries} lần")
        print()

        # Start progress printer thread
        progress_thread = threading.Thread(target=self.print_progress, daemon=True)
        progress_thread.start()

        # Start worker threads
        workers = []
        for i in range(self.max_workers):
            worker = threading.Thread(target=self.worker_thread, args=(i + 1,))
            worker.start()
            workers.append(worker)

        try:
            # Wait for all URLs to be processed
            self.url_queue.join()

            # Send poison pills to workers
            for _ in workers:
                self.url_queue.put(None)

            # Wait for workers to finish
            for worker in workers:
                worker.join()

        except KeyboardInterrupt:
            print("\n\n⚠️  Nhận Ctrl+C, đang dừng...")

            # Send poison pills
            for _ in workers:
                self.url_queue.put(None)

            # Wait for workers to finish
            for worker in workers:
                worker.join()

            print("✅ Đã dừng an toàn")

        # Final statistics
        with self.stats_lock:
            self.stats['end_time'] = time.time()
            duration = self.stats['end_time'] - self.stats['start_time']

            print("\n" + "=" * 60)
            print("📊 THỐNG KÊ CUỐI CÙNG")
            print("=" * 60)
            print(f"Tổng số URL: {self.stats['total']}")
            print(f"✅ Hoàn thành: {self.stats['completed']}")
            print(f"❌ Thất bại: {self.stats['failed']}")
            print(f"⏭️  Bỏ qua: {self.stats['skipped']}")
            print(f"⏱️  Thời gian: {duration:.1f}s")

            if self.stats['completed'] > 0:
                avg_time = duration / self.stats['completed']
                print(f"🚀 Tốc độ: {avg_time:.1f}s/URL")

        # Save state for resume
        self.save_resume_state()

        # Save failed URLs
        if self.failed_urls:
            failed_file = os.path.join(self.output_dir, "failed_urls.txt")
            with open(failed_file, 'w', encoding='utf-8') as f:
                for url in self.failed_urls:
                    f.write(f"{url}\n")
            print(f"💾 Đã lưu URL thất bại vào: {failed_file}")

        print(f"📁 Output directory: {self.output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-threaded batch crawler for thuvienphapluat.vn",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python batch_crawler.py urls.txt
  python batch_crawler.py urls.txt --threads 8 --cookies cookies.txt
  python batch_crawler.py urls.txt --threads 4 --delay 2 5 --retry 2
  python batch_crawler.py urls.txt --resume
        """
    )

    parser.add_argument("url_file", help="File containing list of URLs (one per line)")
    parser.add_argument("-t", "--threads", type=int, default=4,
                       help="Number of concurrent threads (default: 4)")
    parser.add_argument("-c", "--cookies", default="cookies.txt",
                       help="Cookie file (default: cookies.txt)")
    parser.add_argument("-d", "--delay", nargs=2, type=float, default=[3.0, 5.0],
                   metavar=("MIN", "MAX"), help="Delay range between requests (default: 5.0 10.0)")
    parser.add_argument("-r", "--retry", type=int, default=3,
                       help="Number of retries per URL (default: 3)")
    parser.add_argument("--resume", action="store_true",
                       help="Resume from previous run")
    parser.add_argument("--state", default="crawl_state.json",
                       help="State file for resume functionality (default: crawl_state.json)")
    parser.add_argument("-o", "--output-dir", default="crawl",
                       help="Output directory for crawled files (default: crawl)")

    args = parser.parse_args()

    # Validate arguments
    if not os.path.exists(args.url_file):
        print(f"❌ File không tồn tại: {args.url_file}")
        sys.exit(1)

    if args.threads < 1:
        print("❌ Số threads phải >= 1")
        sys.exit(1)

    if args.retry < 0:
        print("❌ Số lần thử lại phải >= 0")
        sys.exit(1)

    # Create and run crawler
    crawler = BatchCrawler(
        max_workers=args.threads,
        cookie_file=args.cookies,
        delay_range=tuple(args.delay),
        max_retries=args.retry,
        output_dir=args.output_dir
    )

    try:
        crawler.run(args.url_file, args.resume)
    except KeyboardInterrupt:
        print("\n\n🛑 Dừng bởi người dùng")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Lỗi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()