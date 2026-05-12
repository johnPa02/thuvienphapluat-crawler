import json
import sys
from pathlib import Path

INPUT_FILE = "mapping_forms.json"
OUTPUT_FILE = "mapping_forms_standard.json"


def convert_json(input_file, output_file):

    if not Path(input_file).exists():
        print(f"❌ File not found: {input_file}")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = []

    # data dạng dict
    for old_url, new_url in data.items():

        results.append({
            "old": old_url,
            "new": new_url
        })

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("✅ Convert done")
    print(f"Records: {len(results)}")
    print(f"Saved: {output_file}")


if __name__ == "__main__":
    convert_json(INPUT_FILE, OUTPUT_FILE)