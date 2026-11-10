import io
import re
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

_TAX_ID = re.compile(r"(?i)\b(?:EIN|Tax\s*ID|TIN|VAT\s*ID)\s*[:#]?\s*\d{2}[-\s]\d{7}\b")


def _ocr_page(pdf_path: Path, page_number: int, *, footer_height: float | None = None, page_width: float = 0, page_height: float = 0) -> str:
    with tempfile.TemporaryDirectory() as rendered:
        target = Path(rendered) / "page"
        command = ["pdftoppm", "-f", str(page_number), "-l", str(page_number), "-r", "300" if footer_height else "200", "-gray", "-singlefile"]
        if footer_height:
            scale = 300 / 72
            command += ["-x", "0", "-y", str(max(0, int((page_height - footer_height) * scale))), "-W", str(int(page_width * scale)), "-H", str(int(footer_height * scale))]
        subprocess.run([*command, str(pdf_path), str(target)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        result = subprocess.run(["tesseract", str(target) + ".pgm", "stdout", "--psm", "6" if footer_height else "3"], check=True, capture_output=True, text=True, timeout=20)
        return result.stdout.strip()


def _ocr_evidence(pdf_bytes: bytes, reader: PdfReader) -> str:
    with tempfile.TemporaryDirectory() as directory:
        pdf_path = Path(directory) / "quote.pdf"
        pdf_path.write_bytes(pdf_bytes)
        # Search the first and last footer only; full-page OCR would duplicate order lines.
        for index in dict.fromkeys((len(reader.pages) - 1, 0)):
            page = reader.pages[index]
            text = _ocr_page(pdf_path, index + 1, footer_height=135, page_width=float(page.mediabox.width), page_height=float(page.mediabox.height))
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            for position, line in enumerate(lines):
                if _TAX_ID.search(line):
                    seller = lines[position - 1] if position else ""
                    return f"[OCR footer evidence; verify against PDF]\n{seller}\n{line}"
    return ""


def extract_text(pdf_bytes: bytes) -> str:
    """Extract page-ordered text, using OCR for pages without selectable text."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) > 50:
        raise ValueError("PDF exceeds the 50-page limit")
    pages = []
    text_length = 0
    for page in reader.pages:
        page_text = page.extract_text(extraction_mode="layout") or ""
        text_length += len(page_text)
        if text_length > 100_000:
            raise ValueError("PDF text exceeds the 100,000-character limit")
        pages.append(page_text)
    scanned = [index for index, text in enumerate(pages) if not text.strip()]
    if len(scanned) > 10:
        raise ValueError("Scanned PDF exceeds the 10-page OCR limit")
    if scanned:
        try:
            with tempfile.TemporaryDirectory() as directory:
                pdf_path = Path(directory) / "quote.pdf"
                pdf_path.write_bytes(pdf_bytes)
                for index in scanned:
                    page_text = _ocr_page(pdf_path, index + 1)
                    if page_text:
                        pages[index] = f"[OCR page {index + 1}; verify against PDF]\n{page_text}"
                        text_length += len(pages[index])
                        if text_length > 100_000:
                            raise ValueError("PDF text exceeds the 100,000-character limit")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # Missing OCR on any page must not silently produce a partial quote.
            raise ValueError("OCR failed; enter the PDF details manually") from exc
    text = "\n".join(pages)
    if text.strip() and not _TAX_ID.search(text):
        try:
            evidence = _ocr_evidence(pdf_bytes, reader)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            evidence = ""
        if evidence:
            text = f"{text}\n\n{evidence}"
    if len(text) > 100_000:
        raise ValueError("PDF text exceeds the 100,000-character limit")
    return text
