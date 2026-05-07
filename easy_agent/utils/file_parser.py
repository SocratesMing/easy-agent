"""File content parser - extracts text from various file formats"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def parse_file_content(file_path: str, mime_type: str = "") -> str:
    ext = os.path.splitext(file_path)[1].lower()
    path = Path(file_path)

    if not path.exists():
        logger.warning(f"文件不存在: {file_path}")
        return ""

    try:
        if ext == ".pdf":
            return _parse_pdf(path)
        elif ext in (".docx", ".doc"):
            return _parse_docx(path)
        elif ext in (".xlsx", ".xls"):
            return _parse_xlsx(path)
        elif ext in (".pptx", ".ppt"):
            return _parse_pptx(path)
        elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"):
            return _parse_image(path)
        elif ext in (".csv",):
            return _parse_csv(path)
        elif ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
                     ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
                     ".kt", ".scala", ".sh", ".bash", ".zsh", ".sql", ".r", ".m",
                     ".yaml", ".yml", ".json", ".xml", ".html", ".css", ".scss",
                     ".less", ".vue", ".svelte", ".md", ".rst", ".txt", ".ini",
                     ".cfg", ".conf", ".toml", ".env", ".gitignore", ".dockerfile",
                     ".proto", ".graphql", ".tf", ".gradle", ".properties"):
            return _parse_text(path)
        else:
            return _parse_text(path)
    except Exception as e:
        logger.warning(f"解析文件失败 {file_path}: {e}")
        return f"[文件解析失败: {e}]"


def _parse_text(path: Path) -> str:
    encodings = ["utf-8", "gbk", "gb2312", "latin-1", "shift-jis", "big5"]
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_text(encoding="latin-1", errors="replace")


def _parse_pdf(path: Path) -> str:
    try:
        import fitz
        doc = fitz.open(str(path))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        if text.strip():
            return text
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"PyMuPDF 解析失败: {e}")

    try:
        from pdfminer.high_level import extract_text
        text = extract_text(str(path))
        if text.strip():
            return text
    except ImportError:
        pass

    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if text.strip():
                return text
    except ImportError:
        pass

    return f"[PDF文件: {path.name}，未安装PDF解析库，请安装: pip install pymupdf pdfminer.six pdfplumber]"


def _parse_docx(path: Path) -> str:
    try:
        from docx import Document
        doc = Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
        if text.strip():
            return text
    except ImportError:
        pass
    return f"[DOCX文件: {path.name}，未安装python-docx，请安装: pip install python-docx]"


def _parse_xlsx(path: Path) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), data_only=True)
        text_parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            text_parts.append(f"--- Sheet: {sheet_name} ---")
            for row in ws.iter_rows(values_only=True):
                row_text = "\t".join(str(cell) if cell is not None else "" for cell in row)
                if row_text.strip():
                    text_parts.append(row_text)
        text = "\n".join(text_parts)
        if text.strip():
            return text
    except ImportError:
        pass
    return f"[XLSX文件: {path.name}，未安装openpyxl，请安装: pip install openpyxl]"


def _parse_pptx(path: Path) -> str:
    try:
        from pptx import Presentation
        prs = Presentation(str(path))
        text_parts = []
        for i, slide in enumerate(prs.slides):
            text_parts.append(f"--- Slide {i + 1} ---")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text_parts.append(shape.text)
        text = "\n".join(text_parts)
        if text.strip():
            return text
    except ImportError:
        pass
    return f"[PPTX文件: {path.name}，未安装python-pptx，请安装: pip install python-pptx]"


def _parse_image(path: Path) -> str:
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(str(path))
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        if text.strip():
            return text
    except ImportError:
        pass
    return f"[图片文件: {path.name}，未安装OCR库，请安装: pip install pytesseract pillow]"


def _parse_csv(path: Path) -> str:
    try:
        import csv
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)
            if rows:
                return "\n".join(",".join(row) for row in rows)
    except Exception:
        pass
    return _parse_text(path)
