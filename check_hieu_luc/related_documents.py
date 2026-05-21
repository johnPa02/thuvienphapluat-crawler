from urllib.parse import urljoin, urlsplit, urlunsplit


RELATED_DOCUMENT_SECTIONS = (
    {
        "toggle_id": "replaceDocument",
        "section": "Văn bản thay thế",
        "relation_type": "replacement",
        "aliases": ("Văn bản thay thế",),
    },
    {
        "toggle_id": "guidedDocument",
        "section": "Văn bản được hướng dẫn",
        "relation_type": "guided_by",
        "aliases": ("Văn bản được hướng dẫn",),
    },
    {
        "toggle_id": "DuocHopNhatDocument",
        "section": "Văn bản được hợp nhất",
        "relation_type": "consolidated_by",
        "aliases": ("Văn bản được hợp nhất",),
    },
    {
        "toggle_id": "amendedDocument",
        "section": "Văn bản bị sửa đổi bổ sung",
        "relation_type": "amended_by",
        "aliases": ("Văn bản bị sửa đổi bổ sung", "Văn bản bị sửa đổi, bổ sung"),
    },
    {
        "toggle_id": "replacedDocument",
        "section": "Văn bản bị thay thế",
        "relation_type": "replaced_by",
        "aliases": ("Văn bản bị thay thế",),
    },
    {
        "toggle_id": "referentialDocument",
        "section": "Văn bản được dẫn chiếu",
        "relation_type": "referenced_by",
        "aliases": ("Văn bản được dẫn chiếu",),
    },
    {
        "toggle_id": "basisDocument",
        "section": "Văn bản được căn cứ",
        "relation_type": "based_by",
        "aliases": ("Văn bản được căn cứ", "Văn bản được căn cứ -"),
    },
    {
        "toggle_id": "HopNhatDocument",
        "section": "Văn bản hợp nhất",
        "relation_type": "consolidating",
        "aliases": ("Văn bản hợp nhất",),
    },
    {
        "toggle_id": "amendDocument",
        "section": "Văn bản sửa đổi bổ sung",
        "relation_type": "amending",
        "aliases": ("Văn bản sửa đổi bổ sung", "Văn bản sửa đổi, bổ sung"),
    },
    {
        "toggle_id": "guideDocument",
        "section": "Văn bản hướng dẫn",
        "relation_type": "guiding",
        "aliases": ("Văn bản hướng dẫn",),
    },
    {
        "toggle_id": "correctingDocument",
        "section": "Văn bản đính chính",
        "relation_type": "correcting",
        "aliases": ("Văn bản đính chính",),
    },
    {
        "toggle_id": "correctedDocument",
        "section": "Văn bản được đính chính",
        "relation_type": "corrected_by",
        "aliases": ("Văn bản được đính chính",),
    },
)


def normalize_text(text: str) -> str:
    return " ".join(text.split()) if text else ""


def normalize_section_title(text: str) -> str:
    text = normalize_text(text).lower()
    text = text.replace(",", " ")
    text = text.replace("-", " ")
    text = text.replace(":", " ")
    text = text.replace("[", " ")
    text = text.replace("]", " ")
    return normalize_text(text)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def is_valid_related_url(url: str, allowed_domains=None) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return False
    if allowed_domains and not any(parts.netloc.lower().endswith(domain) for domain in allowed_domains):
        return False
    return True


async def _reveal_more_links_js(page, toggle_id):
    # Dùng native JS click thay vì Playwright synthetic click để tránh bot detection.
    # dgcvm button có thể load thêm items qua AJAX hoặc CSS show/hide.
    await page.evaluate(f"""
        () => {{
            const container = document.querySelector('#{toggle_id}');
            if (!container) return;
            for (const btn of container.querySelectorAll('.dgcvm')) {{
                btn.click();
            }}
        }}
    """)
    await page.wait_for_timeout(300)


async def extract_section_documents(page, current_url, section, *, allowed_domains=None):
    toggle_id = section["toggle_id"]

    # Kiểm tra container tồn tại — không cần click header vì DOM đã có data sẵn.
    # Accordion chỉ ẩn container qua CSS; query_selector_all đọc được hidden elements.
    container_exists = await page.evaluate(
        f"() => !!document.querySelector('#{toggle_id}')"
    )
    if not container_exists:
        return [], "NO_SECTION"

    # Trigger "Xem thêm" bằng native JS nếu có
    await _reveal_more_links_js(page, toggle_id)

    # Đọc links + tooltip status bằng JS evaluate.
    # Tooltip (display:none) đã có sẵn Tình trạng/Số hiệu → không cần navigate sang related URL.
    links_data = await page.evaluate(f"""
        () => {{
            const container = document.querySelector('#{toggle_id}');
            if (!container) return [];
            return Array.from(container.querySelectorAll('.dgc')).map(dgc => {{
                const a = dgc.querySelector('div:first-child a[href]');
                if (!a) return null;

                // Đọc metadata từ tooltip rows
                let rawStatus = '', soHieu = '', ngayHieuLuc = '', ngayBanHanh = '';
                for (const row of dgc.querySelectorAll('div[style*="border: solid 1px"]')) {{
                    const cells = row.querySelectorAll(':scope > div[style*="float"]');
                    if (cells.length >= 2) {{
                        const label = cells[0].textContent.trim().replace(':', '').trim();
                        const value = cells[1].textContent.trim();
                        if (label === 'Tình trạng') rawStatus = value;
                        else if (label === 'Số hiệu') soHieu = value;
                        else if (label === 'Ngày hiệu lực') ngayHieuLuc = value;
                        else if (label === 'Ngày ban hành') ngayBanHanh = value;
                    }}
                }}

                return {{
                    href: (a.getAttribute('href') || '').trim(),
                    title: (a.textContent || '').trim(),
                    tooltip_raw_status: rawStatus,
                    tooltip_so_hieu: soHieu,
                    tooltip_effective_date: ngayHieuLuc || ngayBanHanh,
                }};
            }}).filter(Boolean);
        }}
    """)

    current_key = canonicalize_url(current_url)
    documents = []
    seen = set()

    for item in links_data:
        href = normalize_text(item.get("href", ""))
        title = normalize_text(item.get("title", ""))
        absolute_url = urljoin(current_url, href)

        if not is_valid_related_url(absolute_url, allowed_domains):
            continue

        url_key = canonicalize_url(absolute_url)
        if url_key == current_key or url_key in seen:
            continue

        seen.add(url_key)
        documents.append(
            {
                "title": title,
                "url": absolute_url,
                "url_key": url_key,
                "source_section": section["section"],
                "source_sections": [section["section"]],
                "source_toggle": toggle_id,
                "source_toggles": [toggle_id],
                "relation_type": section["relation_type"],
                "relation_types": [section["relation_type"]],
                "depth": 1,
                "tooltip_raw_status": item.get("tooltip_raw_status", ""),
                "tooltip_so_hieu": item.get("tooltip_so_hieu", ""),
                "tooltip_effective_date": item.get("tooltip_effective_date", ""),
            }
        )

    return documents, None


async def collect_related_documents(
    page,
    current_url,
    *,
    sections=RELATED_DOCUMENT_SECTIONS,
    allowed_domains=("thuvienphapluat.vn",),
    deduplicate=True,
    preserve_source_section=True,
    log_func=None,
):
    collected = []
    by_url = {}

    for section in sections:
        documents, error = await extract_section_documents(
            page,
            current_url,
            section,
            allowed_domains=allowed_domains,
        )
        if error:
            if log_func and error != "NO_SECTION":
                log_func(f"Related section skipped: {section['section']} [{section['toggle_id']}] -> {error}")
            continue

        if log_func:
            log_func(f"Related section: {section['section']} [{section['toggle_id']}] -> {len(documents)} URL(s)")

        for document in documents:
            url_key = document["url_key"]
            existing = by_url.get(url_key)
            if deduplicate and existing:
                for field, values in (
                    ("source_sections", document["source_sections"]),
                    ("source_toggles", document["source_toggles"]),
                    ("relation_types", document["relation_types"]),
                ):
                    for value in values:
                        if value not in existing[field]:
                            existing[field].append(value)
                continue

            if not preserve_source_section:
                document["source_section"] = ""
                document["source_sections"] = []
                document["source_toggle"] = ""
                document["source_toggles"] = []
                document["relation_type"] = ""
                document["relation_types"] = []

            if deduplicate:
                by_url[url_key] = document
            collected.append(document)

    return collected
