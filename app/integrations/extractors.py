"""Extração de texto de documentos (PDF, DOCX, TXT, MD), organizada por página."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import BusinessValidationError

logger = logging.getLogger(__name__)


@dataclass
class ExtractedPage:
    number: int | None  # None quando o formato não tem paginação (DOCX/TXT)
    text: str


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BusinessValidationError("Não foi possível decodificar o arquivo de texto.")  # pragma: no cover


def _extract_pdf(path: Path) -> list[ExtractedPage]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise BusinessValidationError("O PDF está protegido por senha e não pode ser lido.")
        return [ExtractedPage(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    except PdfReadError as exc:
        raise BusinessValidationError(f"PDF inválido ou corrompido: {exc}") from exc


def _extract_docx(path: Path) -> list[ExtractedPage]:
    import docx  # python-docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # python-docx lança tipos variados para arquivos inválidos
        raise BusinessValidationError(f"DOCX inválido ou corrompido: {exc}") from exc
    blocks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return [ExtractedPage(None, "\n\n".join(blocks))]


def extract_pages(path: Path) -> list[ExtractedPage]:
    """Extrai o texto do arquivo. Lança BusinessValidationError se não houver texto extraível."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        pages = _extract_pdf(path)
    elif ext == ".docx":
        pages = _extract_docx(path)
    elif ext in (".txt", ".md"):
        pages = [ExtractedPage(None, _read_text_file(path))]
    else:
        raise BusinessValidationError(f"Formato não suportado para extração: {ext}")
    if not any(p.text.strip() for p in pages):
        raise BusinessValidationError(
            "O documento não possui texto extraível (pode ser um PDF digitalizado/imagem). "
            "Nenhum conteúdo dele será usado na geração."
        )
    logger.info("Documento extraído: %s página(s)", len(pages))
    return pages
