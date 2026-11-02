import hashlib
import csv
import json
import os
import re
from pathlib import Path

from pypdf import PdfReader
from openpyxl import load_workbook
import xlrd
from docx import Document
from pptx import Presentation

import shutil as _shutil

from PIL import Image, ImageOps
import pytesseract
import pymupdf  # rasterizes PDF pages without needing poppler installed



# ============================================================
# FAST INDEX METADATA
# ============================================================

def _normalized_content_for_hash(content):
    return re.sub(r"\s+", " ", str(content or "").strip())


def content_hash(content):
    return hashlib.sha256(
        _normalized_content_for_hash(content).encode("utf-8")
    ).hexdigest()


def stable_record_id(record):
    payload = {
        "source": record.get("source"),
        "path": record.get("path"),
        "file_type": record.get("file_type"),
        "page": record.get("page"),
        "sheet": record.get("sheet"),
        "row": record.get("row"),
        "slide": record.get("slide"),
        "section": record.get("section"),
        "content_hash": content_hash(record.get("content", "")),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def enrich_record(record):
    if not record:
        return record

    record = dict(record)
    content = clean_text(record.get("content", ""))
    metadata = dict(record.get("metadata") or {})

    metadata["content_hash"] = content_hash(content)
    metadata["token_count"] = len(re.findall(r"\b\w+\b", content))
    metadata["has_numbers"] = bool(
        re.search(r"\b\d[\d,]*(?:\.\d+)?%?\b", content)
    )
    metadata["has_year"] = bool(re.search(r"\b(?:19|20)\d{2}\b", content))
    metadata["ocr"] = bool(metadata.get("ocr", False))

    record["content"] = content
    record["metadata"] = metadata
    record["record_id"] = stable_record_id(record)
    return record


def normalize_records(records):
    """
    Deduplicate extracted records once, preserving deterministic order.
    """
    seen = set()
    result = []

    for record in records or []:
        enriched = enrich_record(record)
        if not enriched:
            continue

        rid = enriched["record_id"]
        if rid in seen:
            continue

        seen.add(rid)
        result.append(enriched)

    return result


# ============================================================
# TESSERACT DISCOVERY
# ============================================================
# The previous hardcoded path (C:\Program Files\Tesseract-OCR\tesseract.exe)
# only worked on a Windows machine with Tesseract installed at that exact
# location. On any other machine -- a different drive, a different OS, a
# portable/custom install -- every OCR call silently failed inside the
# try/except in ocr_image() below, so scanned PDFs and images were
# indexed as empty records with no obvious error until someone asked a
# question the OCR text would have answered.
#
# Resolution order: an explicit KORA_TESSERACT_CMD env var, then whatever
# is already on PATH, then the historical Windows default as a last
# resort (only used if that exact file exists).
_TESSERACT_CANDIDATES = [
    os.environ.get("KORA_TESSERACT_CMD"),
    _shutil.which("tesseract"),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
]

_TESSERACT_CMD = next(
    (candidate for candidate in _TESSERACT_CANDIDATES
     if candidate and Path(candidate).exists()),
    None,
)

if _TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
else:
    print(
        "[OCR] Could not find a Tesseract install (checked "
        "KORA_TESSERACT_CMD, PATH, and the default Windows install "
        "path). Scanned PDFs and images will index as empty records "
        "until Tesseract is installed and on PATH, or KORA_TESSERACT_CMD "
        "is set to its full path."
    )


# ============================================================
# SUPPORTED FILE TYPES
# ============================================================

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xlsx",
    ".xls",
    ".docx",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tiff",
    ".webp",
}

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tiff",
    ".webp",
}

# A page whose embedded text layer is shorter than this is treated as
# "probably scanned" and gets OCR'd instead.
MIN_EMBEDDED_TEXT_CHARS = 20

# Pages/images are upscaled to at least this width before OCR — small
# or low-DPI text is where Tesseract's accuracy drops off fastest.
OCR_MIN_WIDTH_PX = 1600


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(text):

    if text is None:
        return ""

    text = str(text)

    # Normalize whitespace but preserve meaningful line breaks
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    lines = []

    for line in text.split("\n"):

        line = re.sub(
            r"[ \t]+",
            " ",
            line
        ).strip()

        if line:
            lines.append(line)

    return "\n".join(lines).strip()


# ============================================================
# KNOWLEDGE RECORD
# ============================================================

def make_record(
    path,
    file_type,
    content,
    page=None,
    sheet=None,
    row=None,
    slide=None,
    section=None,
    metadata=None,
):

    content = clean_text(content)

    if not content:
        return None

    record = {
        "source": path.name,
        "path": str(path),
        "file_type": file_type,

        "page": page,
        "sheet": sheet,
        "row": row,
        "slide": slide,
        "section": section,

        "content": content,

        "metadata": metadata or {},
    }

    return enrich_record(record)


# ============================================================
# OCR (images, and scanned PDF pages)
# ============================================================

def preprocess_for_ocr(image):
    """
    Cheap, effective preprocessing that measurably improves Tesseract
    accuracy: convert to grayscale, auto-contrast, and upscale small
    images. No binarization threshold is applied — Tesseract's own
    adaptive thresholding (its default page segmentation) generally
    outperforms a fixed threshold across varied lighting/scan quality,
    so we leave that step to it.
    """

    image = image.convert("L")

    image = ImageOps.autocontrast(image)

    if image.width < OCR_MIN_WIDTH_PX:

        scale = OCR_MIN_WIDTH_PX / image.width

        image = image.resize(
            (
                int(image.width * scale),
                int(image.height * scale),
            ),
            Image.LANCZOS,
        )

    return image


def ocr_image(image):

    image = preprocess_for_ocr(image)

    try:

        text = pytesseract.image_to_string(image)

    except Exception as error:

        # Silently returning "" here means a broken Tesseract install
        # looks identical to "this image has no text" -- both end up
        # as an empty, un-indexed record with no visible error. Print
        # it so a setup problem is obvious in the console instead of
        # showing up as a mysteriously unanswerable question later.
        print(f"[OCR] Failed to run Tesseract: {error}")
        print(
            "[OCR] Is Tesseract installed and on PATH? Run "
            "test_ocr.py to check, or see the README for setup."
        )
        text = ""

    return text


def extract_image(path):

    try:

        image = Image.open(path)

    except Exception as error:

        raise RuntimeError(f"Could not open image {path.name}: {error}") from error

    text = ocr_image(image)

    record = make_record(
        path=path,
        file_type="image",
        content=text,
        metadata={"ocr": True, "ocr_text": True},
    )

    if record:

        return [record]

    return []


# ============================================================
# PDF
# ============================================================

def extract_pdf(path):

    records = []

    reader = PdfReader(str(path))

    # Only open the PDF a second time (via PyMuPDF, for rasterizing
    # scanned pages) if we actually hit a page that needs it.
    ocr_document = None

    try:

        for page_number, page in enumerate(
            reader.pages,
            start=1
        ):

            try:

                text = page.extract_text() or ""

            except Exception:

                text = ""

            metadata = None

            # A near-empty embedded text layer usually means the page
            # is a scanned image rather than real text — fall back to
            # OCR on a rendered image of that page.
            if len(text.strip()) < MIN_EMBEDDED_TEXT_CHARS:

                try:

                    if ocr_document is None:

                        ocr_document = pymupdf.open(str(path))

                    pdf_page = ocr_document.load_page(page_number - 1)

                    # 2x zoom roughly matches ~150 DPI, then
                    # preprocess_for_ocr upscales further if still small.
                    pixmap = pdf_page.get_pixmap(
                        matrix=pymupdf.Matrix(2, 2)
                    )

                    image = Image.frombytes(
                        "RGB",
                        [pixmap.width, pixmap.height],
                        pixmap.samples,
                    )

                    ocr_text = ocr_image(image)

                    if len(ocr_text.strip()) > len(text.strip()):

                        text = ocr_text
                        metadata = {"ocr": True}

                except Exception:

                    pass

            record = make_record(
                path=path,
                file_type="pdf",
                content=text,
                page=page_number,
                metadata=metadata,
            )

            if record:

                records.append(record)

    finally:

        if ocr_document is not None:

            ocr_document.close()

    return records


# ============================================================
# TXT / MARKDOWN
# ============================================================

def extract_text(path):

    try:

        text = path.read_text(
            encoding="utf-8",
            errors="ignore"
        )

    except Exception as error:

        raise RuntimeError(f"Could not read text file {path.name}: {error}") from error

    record = make_record(
        path=path,
        file_type=path.suffix.lower().lstrip("."),
        content=text,
    )

    if record:

        return [record]

    return []


# ============================================================
# CSV
# ============================================================

def extract_csv(path):

    records = []

    try:

        with open(
            path,
            "r",
            encoding="utf-8-sig",
            errors="ignore",
            newline=""
        ) as file:

            reader = csv.reader(file)

            rows = list(reader)

    except Exception as error:

        raise RuntimeError(f"Could not read CSV {path.name}: {error}") from error

    if not rows:

        return []

    # First non-empty row is treated as the header.
    header_index = next((i for i, row in enumerate(rows) if any(str(v).strip() for v in row)), None)
    if header_index is None:
        return []
    headers = [
        str(value).strip()
        for value in rows[header_index]
    ]

    headers = [
        header if header else f"Column {i + 1}"
        for i, header in enumerate(headers)
    ]

    for row_number, row in enumerate(
        rows[header_index + 1:],
        start=header_index + 2
    ):

        values = list(row)

        # Keep the row length aligned with headers.
        if len(values) < len(headers):

            values.extend(
                [""] * (
                    len(headers) - len(values)
                )
            )

        if len(values) > len(headers):

            values = values[:len(headers)]

        fields = []

        for header, value in zip(
            headers,
            values
        ):

            value = str(value).strip()

            if value:

                fields.append(
                    f"{header}: {value}"
                )

        if not fields:

            continue

        content = "\n".join(fields)

        record = make_record(
            path=path,
            file_type="csv",
            content=content,
            row=row_number,
            metadata={
                "headers": headers
            }
        )

        if record:

            records.append(record)

    return records


# ============================================================
# JSON
# ============================================================

def flatten_json(
    value,
    prefix=""
):

    results = []

    if isinstance(value, dict):

        for key, child in value.items():

            new_prefix = (
                f"{prefix}.{key}"
                if prefix
                else str(key)
            )

            results.extend(
                flatten_json(
                    child,
                    new_prefix
                )
            )

    elif isinstance(value, list):

        for index, child in enumerate(value):

            new_prefix = (
                f"{prefix}[{index}]"
                if prefix
                else f"[{index}]"
            )

            results.extend(
                flatten_json(
                    child,
                    new_prefix
                )
            )

    else:

        results.append(
            (
                prefix,
                str(value)
            )
        )

    return results


def extract_json(path):

    try:

        with open(
            path,
            "r",
            encoding="utf-8-sig",
            errors="ignore"
        ) as file:

            data = json.load(file)

    except Exception as error:

        raise RuntimeError(f"Could not read JSON {path.name}: {error}") from error

    flattened = flatten_json(data)

    if not flattened:

        return []

    lines = []

    for key, value in flattened:

        lines.append(
            f"{key}: {value}"
        )

    content = "\n".join(lines)

    record = make_record(
        path=path,
        file_type="json",
        content=content,
        metadata={
            "structure": "flattened_json"
        }
    )

    if record:

        return [record]

    return []


# ============================================================
# XLSX
# ============================================================

def extract_xlsx(path):

    records = []

    try:

        workbook = load_workbook(
            filename=str(path),
            read_only=True,
            data_only=True
        )

    except Exception as error:

        raise RuntimeError(f"Could not read XLSX {path.name}: {error}") from error

    for worksheet in workbook.worksheets:

        rows = worksheet.iter_rows(
            values_only=True
        )

        # Find the first non-empty row so decorative blank rows do not become headers.
        first_row = None
        header_offset = 0
        for candidate in rows:
            if any(value is not None and str(value).strip() for value in candidate):
                first_row = candidate
                break
            header_offset += 1

        if first_row is None:
            continue

        headers = []

        for index, value in enumerate(
            first_row
        ):

            header = (
                str(value).strip()
                if value is not None
                else ""
            )

            if not header:

                header = (
                    f"Column {index + 1}"
                )

            headers.append(header)

        for row_number, row in enumerate(
            rows,
            start=header_offset + 2
        ):

            values = list(row)

            if len(values) < len(headers):

                values.extend(
                    [None] * (
                        len(headers)
                        - len(values)
                    )
                )

            fields = []

            for header, value in zip(
                headers,
                values
            ):

                if value is None:

                    continue

                value = str(value).strip()

                if not value:

                    continue

                fields.append(
                    f"{header}: {value}"
                )

            if not fields:

                continue

            content = "\n".join(fields)

            record = make_record(
                path=path,
                file_type="xlsx",
                content=content,
                sheet=worksheet.title,
                row=row_number,
                metadata={
                    "headers": headers
                }
            )

            if record:

                records.append(record)

    workbook.close()

    return records


# ============================================================
# XLS
# ============================================================

def extract_xls(path):

    records = []

    try:

        workbook = xlrd.open_workbook(
            str(path)
        )

    except Exception as error:

        raise RuntimeError(f"Could not read XLS {path.name}: {error}") from error

    for sheet in workbook.sheets():

        if sheet.nrows == 0:

            continue

        header_index = 0
        for candidate_index in range(sheet.nrows):
            candidate = sheet.row_values(candidate_index)
            if any(str(value).strip() for value in candidate):
                header_index = candidate_index
                break
        else:
            continue

        first_row = sheet.row_values(header_index)

        headers = []

        for index, value in enumerate(
            first_row
        ):

            header = str(value).strip()

            if not header:

                header = (
                    f"Column {index + 1}"
                )

            headers.append(header)

        for row_number in range(
            header_index + 1,
            sheet.nrows
        ):

            values = sheet.row_values(
                row_number
            )

            fields = []

            for header, value in zip(
                headers,
                values
            ):

                if value is None:

                    continue

                value = str(value).strip()

                if not value:

                    continue

                fields.append(
                    f"{header}: {value}"
                )

            if not fields:

                continue

            content = "\n".join(fields)

            record = make_record(
                path=path,
                file_type="xls",
                content=content,
                sheet=sheet.name,
                row=row_number + 1,
                metadata={
                    "headers": headers
                }
            )

            if record:

                records.append(record)

    return records


# ============================================================
# DOCX
# ============================================================

def extract_docx(path):

    records = []

    try:

        document = Document(
            str(path)
        )

    except Exception as error:

        raise RuntimeError(f"Could not read DOCX {path.name}: {error}") from error

    # --------------------------------------------------------
    # Paragraphs
    # --------------------------------------------------------

    current_section = None

    for paragraph in document.paragraphs:

        text = clean_text(
            paragraph.text
        )

        if not text:

            continue

        style_name = ""

        try:

            style_name = (
                paragraph.style.name
                or ""
            )

        except Exception:

            pass

        if (
            "Heading" in style_name
            or style_name.lower() in {
                "title",
                "subtitle"
            }
        ):

            current_section = text

        record = make_record(
            path=path,
            file_type="docx",
            content=text,
            section=current_section,
            metadata={
                "element": "paragraph",
                "style": style_name
            }
        )

        if record:

            records.append(record)

    # --------------------------------------------------------
    # Tables
    # --------------------------------------------------------

    for table_index, table in enumerate(
        document.tables,
        start=1
    ):

        for row_index, row in enumerate(
            table.rows,
            start=1
        ):

            cells = []

            for cell_index, cell in enumerate(
                row.cells,
                start=1
            ):

                text = clean_text(
                    cell.text
                )

                if text:

                    cells.append(
                        f"Column {cell_index}: {text}"
                    )

            if not cells:

                continue

            content = "\n".join(cells)

            record = make_record(
                path=path,
                file_type="docx",
                content=content,
                row=row_index,
                metadata={
                    "element": "table",
                    "table": table_index
                }
            )

            if record:

                records.append(record)

    return records


# ============================================================
# PPTX
# ============================================================

def extract_pptx(path):

    records = []

    try:

        presentation = Presentation(
            str(path)
        )

    except Exception as error:

        raise RuntimeError(f"Could not read PPTX {path.name}: {error}") from error

    for slide_number, slide in enumerate(
        presentation.slides,
        start=1
    ):

        parts = []

        for shape in slide.shapes:

            # Normal text boxes
            if hasattr(
                shape,
                "text"
            ):

                text = clean_text(
                    shape.text
                )

                if text:

                    parts.append(text)

            # Tables
            if getattr(
                shape,
                "has_table",
                False
            ):

                table = shape.table

                for row in table.rows:

                    cells = []

                    for cell in row.cells:

                        text = clean_text(
                            cell.text
                        )

                        if text:

                            cells.append(text)

                    if cells:

                        parts.append(
                            " | ".join(cells)
                        )

        content = "\n".join(parts)

        if not content:

            continue

        record = make_record(
            path=path,
            file_type="pptx",
            content=content,
            slide=slide_number,
        )

        if record:

            records.append(record)

    return records


# ============================================================
# UNIVERSAL EXTRACTOR
# ============================================================

def extract_file(path):

    path = Path(path)

    extension = path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:

        return []

    if extension == ".pdf":

        return extract_pdf(path)

    if extension in {
        ".txt",
        ".md"
    }:

        return extract_text(path)

    if extension == ".csv":

        return extract_csv(path)

    if extension == ".json":

        return extract_json(path)

    if extension == ".xlsx":

        return extract_xlsx(path)

    if extension == ".xls":

        return extract_xls(path)

    if extension == ".docx":

        return extract_docx(path)

    if extension == ".pptx":

        return extract_pptx(path)

    if extension in IMAGE_EXTENSIONS:

        return extract_image(path)

    return []


# ============================================================
# TEST / DEBUG
# ============================================================

def print_records(records):

    print()
    print("=" * 60)
    print("KORA INGESTION TEST")
    print("=" * 60)

    print(
        f"Records extracted: {len(records)}"
    )

    print()

    for index, record in enumerate(
        records[:10],
        start=1
    ):

        print(
            f"[{index}] "
            f"{record['source']} "
            f"({record['file_type']})"
        )

        if record.get("page"):

            print(
                f"    Page: {record['page']}"
            )

        if record.get("sheet"):

            print(
                f"    Sheet: {record['sheet']}"
            )

        if record.get("row"):

            print(
                f"    Row: {record['row']}"
            )

        if record.get("slide"):

            print(
                f"    Slide: {record['slide']}"
            )

        print(
            "    Content:"
        )

        print(
            "    "
            + record["content"][:500]
            .replace(
                "\n",
                "\n    "
            )
        )

        print("-" * 60)


# ============================================================
# DIRECT TEST
# ============================================================

if __name__ == "__main__":

    import sys

    if len(sys.argv) < 2:

        print(
            "Usage:"
        )

        print(
            "python src/extractor.py <file>"
        )

        print()

        print(
            "Example:"
        )

        print(
            "python src/extractor.py test.xlsx"
        )

        raise SystemExit(1)

    test_file = Path(
        sys.argv[1]
    )

    if not test_file.exists():

        print(
            f"File not found: {test_file}"
        )

        raise SystemExit(1)

    extracted = extract_file(
        test_file
    )

    print_records(
        extracted
    )