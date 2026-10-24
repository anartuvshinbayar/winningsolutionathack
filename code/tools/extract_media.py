"""Build the text cache for screenshot and PDF evidence (run once; results are committed).

Screenshots: macOS Vision OCR (code/tools/ocr_macos.swift), falling back to Claude vision
when ANTHROPIC_API_KEY is set and the OCR tool is unavailable.
PDFs: pdftotext -layout.
Output: code/cache/media/<file_id>.txt
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import CACHE_DIR, DATASET, read_csv  # noqa: E402

OCR = Path(__file__).with_name("ocr_macos.swift")


def extract(path):
    if path.suffix.lower() == ".pdf":
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    if shutil.which("swift"):
        return subprocess.run(["swift", str(OCR), str(path)], capture_output=True, text=True, check=True).stdout
    from audit.llm import vision_transcribe
    return vision_transcribe(path)


def main():
    out_dir = CACHE_DIR / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    for row in read_csv("evidence_files.csv"):
        path = DATASET / row["file_path"]
        if path.suffix.lower() not in (".png", ".pdf"):
            continue
        target = out_dir / f"{row['file_id']}.txt"
        if target.exists() and "--force" not in sys.argv:
            continue
        target.write_text(extract(path), encoding="utf-8")
        print("cached", row["file_id"], row["file_path"])


if __name__ == "__main__":
    main()
