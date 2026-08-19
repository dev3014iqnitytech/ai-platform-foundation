"""
Ingestion Loaders — Document loaders for PDF, DOCX, Markdown, HTML, and TXT.
Each loader normalizes output to a common dict format for the chunking pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path
from structlog import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt", ".html", ".htm"}


def load_document(file_path: str | Path) -> list[dict]:
    """
    Auto-detect file type and load content.
    Returns a list of page/section dicts: {page: int, content: str, metadata: dict}
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {SUPPORTED_EXTENSIONS}")

    loaders = {
        ".pdf": _load_pdf,
        ".docx": _load_docx,
        ".md": _load_markdown,
        ".txt": _load_text,
        ".html": _load_html,
        ".htm": _load_html,
    }

    loader_fn = loaders[ext]
    pages = loader_fn(path)
    logger.info("document_loaded", filename=path.name, pages=len(pages), type=ext)
    return pages


def _load_pdf(path: Path) -> list[dict]:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({
                    "page": i + 1,
                    "content": text,
                    "metadata": {"source": path.name, "page": i + 1, "type": "pdf"},
                })
        return pages
    except ImportError:
        logger.warning("pypdf_not_installed", fallback="raw_bytes")
        return [{"page": 1, "content": path.read_bytes().decode("utf-8", errors="ignore"), "metadata": {"source": path.name}}]


def _load_docx(path: Path) -> list[dict]:
    try:
        from docx import Document
        doc = Document(str(path))
        sections: list[dict] = []
        current: list[str] = []
        section_num = 1

        for para in doc.paragraphs:
            if para.style.name.startswith("Heading") and current:
                sections.append({
                    "page": section_num,
                    "content": "\n".join(current),
                    "metadata": {"source": path.name, "section": section_num, "type": "docx"},
                })
                current = [para.text]
                section_num += 1
            elif para.text.strip():
                current.append(para.text)

        if current:
            sections.append({
                "page": section_num,
                "content": "\n".join(current),
                "metadata": {"source": path.name, "section": section_num, "type": "docx"},
            })
        return sections or [{"page": 1, "content": "", "metadata": {"source": path.name}}]
    except ImportError:
        logger.warning("python_docx_not_installed")
        return []


def _load_markdown(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    sections = content.split("\n## ")
    pages = []
    for i, section in enumerate(sections):
        text = section.strip()
        if text:
            pages.append({
                "page": i + 1,
                "content": f"## {text}" if i > 0 else text,
                "metadata": {"source": path.name, "section": i + 1, "type": "markdown"},
            })
    return pages or [{"page": 1, "content": content, "metadata": {"source": path.name}}]


def _load_text(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    # Split into ~2000-char chunks to stay under token limits
    chunks, chunk_size = [], 2000
    for i in range(0, len(content), chunk_size):
        chunk = content[i:i + chunk_size].strip()
        if chunk:
            chunks.append({
                "page": i // chunk_size + 1,
                "content": chunk,
                "metadata": {"source": path.name, "chunk": i // chunk_size + 1, "type": "text"},
            })
    return chunks


def _load_html(path: Path) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
        html = path.read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return _load_text_from_string(text, path.name, "html")
    except ImportError:
        logger.warning("beautifulsoup4_not_installed")
        raw = path.read_text(encoding="utf-8", errors="ignore")
        import re
        text = re.sub(r"<[^>]+>", " ", raw)
        return _load_text_from_string(text, path.name, "html")


def _load_text_from_string(text: str, source: str, doc_type: str) -> list[dict]:
    chunks = []
    for i in range(0, len(text), 2000):
        chunk = text[i:i + 2000].strip()
        if chunk:
            chunks.append({
                "page": i // 2000 + 1,
                "content": chunk,
                "metadata": {"source": source, "type": doc_type},
            })
    return chunks
