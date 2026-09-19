"""Text-layer PDF and paragraph/table DOCX readers. No OCR or guessed values."""
from io import BytesIO
from pathlib import Path


def read_document(raw: bytes, path: str):
    suffix = Path(path).suffix.lower()
    if suffix == ".txt":
        return [(line, 1, n) for n, line in enumerate(raw.decode("utf-8").splitlines(), 1)]
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF; provide an unlocked copy")
        if len(reader.pages) > 100:
            raise ValueError("PDF exceeds the 100-page reading limit")
        rows = []
        for page_no, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if not text.strip():
                raise ValueError(f"PDF page {page_no} has no readable text layer; scanned pages require OCR/manual review")
            rows.extend((line, page_no, n) for n, line in enumerate(text.splitlines(), 1))
        return rows
    if suffix == ".docx":
        from docx import Document
        from docx.table import Table
        doc = Document(BytesIO(raw))
        rows = []
        for block in doc.iter_inner_content():
            if isinstance(block, Table):
                for row in block.rows:
                    cells = list(dict.fromkeys(c.text.strip() for c in row.cells))
                    text = cells[0] + ": " + " ".join(cells[1:]) if len(cells) > 1 else " ".join(cells)
                    rows.append((text.replace("\n", " "), None, len(rows) + 1))
            else:
                rows.extend((line, None, len(rows) + n) for n, line in enumerate(block.text.splitlines(), 1))
        if not any(line.strip() for line, _, _ in rows):
            raise ValueError("Word document contains no readable paragraphs or tables")
        return rows
    raise ValueError(f"Unsupported document format: {suffix}")


def document_text(raw: bytes, path: str) -> str:
    return "\n".join(line for line, _, _ in read_document(raw, path))
