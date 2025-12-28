import re

def resolve_footnotes(text: str) -> str:
    lines = text.splitlines()
    main_lines = []
    footnotes = {}  # num -> content
    i = 0

    # 1. Extract footnotes
    while i < len(lines):
        line = lines[i]

        # Match: [1] Nội dung...
        m = re.match(r'^\s*\[(\d+)\]\s*(.*)', line)
        if m:
            num = m.group(1)
            content_lines = [m.group(2)]
            i += 1

            # Collect multiline footnote
            while i < len(lines):
                next_line = lines[i]
                if re.match(r'^\s*\[\d+\]', next_line):
                    break
                content_lines.append(next_line)
                i += 1

            footnotes[num] = "\n".join(content_lines).rstrip()
        else:
            main_lines.append(line)
            i += 1

    main_text = "\n".join(main_lines)

    # 2. Replace [1], [2] everywhere (including nước[1])
    def replace_fn(match):
        num = match.group(1)
        if num in footnotes:
            return f"[{footnotes[num]}]"
        return match.group(0)

    main_text = re.sub(r'\[(\d+)\]', replace_fn, main_text)

    return main_text

# Test
text = """Quốc hội ban hành Luật Ngân sách nhà nước[1].
abc
xyz
[1] Luật Doanh nghiệp số 59/2020/QH14 có căn cứ ban hành như sau:
“Căn cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam;
Quốc hội ban hành Luật Doanh nghiệp.”."""

result = resolve_footnotes(text)
print(result)