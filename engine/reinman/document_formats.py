"""Read layouts and structured tables from uploaded originals."""
from __future__ import annotations

import csv
import io
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from bs4 import BeautifulSoup

from . import programs

MAX_TEXT = 1_000_000
MAX_ROWS = 5_000


def _text(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def _ocr(data: bytes) -> str:
    return subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "3", "-c", "preserve_interword_spaces=1"], input=data,
        capture_output=True, timeout=35, check=True,
    ).stdout.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str:
    text = programs.pdf_layout(data)
    if len("".join(text.split())) >= 20:
        return text
    with TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(["pdftoppm", "-png", "-scale-to", "2000", "-l", "12", "-", str(prefix)],
                       input=data, capture_output=True, timeout=45, check=True)
        return "\f".join(_ocr(page.read_bytes()) for page in sorted(Path(tmp).glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1])))


def _office(data: bytes, suffix: str) -> tuple[str, list[list]]:
    with ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 500 or sum(item.file_size for item in entries) > 32 * 1024 * 1024:
            raise ValueError("Office file expands beyond the supported size.")
    if suffix == ".docx":
        from docx import Document
        doc = Document(io.BytesIO(data))
        tables = [[[cell.text for cell in row.cells] for row in table.rows[:MAX_ROWS]] for table in doc.tables[:30]]
        text = "\n".join(p.text for p in doc.paragraphs)
    else:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
        try:
            tables = [list(sheet.iter_rows(max_row=min(sheet.max_row or MAX_ROWS, MAX_ROWS), max_col=40, values_only=True))
                      for sheet in book.worksheets[:15]]
        finally:
            book.close()
        text = ""
    tables = [[[str(value) if value is not None else "" for value in row] for row in table] for table in tables]
    return text + "\n" + "\n".join(" | ".join(row) for table in tables for row in table if any(row)), tables


def read_document(filename: str, original: bytes | None, fallback: str) -> tuple[str, list[list]]:
    suffix = Path(filename).suffix.lower()
    if original and original.startswith(b"%PDF-"):
        text = _pdf_text(original)[:MAX_TEXT]
        return text, [_spaced_rows(text)]
    if original and suffix in {".docx", ".xlsx"}:
        text, tables = _office(original, suffix)
        return text[:MAX_TEXT], tables
    if original and suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        from PIL import Image
        with Image.open(io.BytesIO(original)) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError("Image exceeds 25 megapixels.")
            image.thumbnail((2500, 2500))
            output = io.BytesIO()
            image.convert("RGB").save(output, "PNG")
        text = _ocr(output.getvalue())[:MAX_TEXT]
        return text, [_spaced_rows(text)]
    if original and suffix in {".html", ".htm"}:
        soup = BeautifulSoup(original, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        tables = [[[cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
                   for row in table.find_all("tr", limit=MAX_ROWS)] for table in soup.find_all("table", limit=30)]
        return (fallback or soup.get_text("\n", strip=True))[:MAX_TEXT], tables
    text = fallback or (_text(original)[:MAX_TEXT] if original else "")
    if suffix in {".csv", ".tsv", ".txt"} and text:
        sample = text[:2048]
        dialect = csv.excel_tab if suffix == ".tsv" or ("\t" in sample and suffix == ".txt") else csv.excel
        try:
            from itertools import islice
            rows = list(islice(csv.reader(io.StringIO(text), dialect=dialect), MAX_ROWS))
            return text, [rows, _spaced_rows(text)] if suffix == ".txt" else [rows]
        except csv.Error:
            return text, []
    return text, []


def _spaced_rows(text: str) -> list[list[str]]:
    return [re.split(r"[ \t]{2,}|\t", line.strip()) for line in text.splitlines()[:MAX_ROWS]]
