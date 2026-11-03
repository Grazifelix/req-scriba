"""Armazenamento de uploads em disco com validação de formato, assinatura e tamanho."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from app.core.errors import BusinessValidationError

CHUNK_BYTES = 1024 * 1024


def _is_mp3(h: bytes) -> bool:
    return h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0)


def _is_ftyp(h: bytes) -> bool:
    return len(h) >= 12 and h[4:8] == b"ftyp"


def _is_text(h: bytes) -> bool:
    return b"\x00" not in h


SIGNATURES: dict[str, Callable[[bytes], bool]] = {
    ".mp3": _is_mp3,
    ".wav": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WAVE",
    ".flac": lambda h: h.startswith(b"fLaC"),
    ".ogg": lambda h: h.startswith(b"OggS"),
    ".webm": lambda h: h.startswith(b"\x1a\x45\xdf\xa3"),
    ".m4a": _is_ftyp,
    ".mp4": _is_ftyp,
    ".pdf": lambda h: h.startswith(b"%PDF-"),
    ".docx": lambda h: h.startswith(b"PK\x03\x04"),
    ".txt": _is_text,
    ".md": _is_text,
}

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".mp4")
DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")

MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
    ".flac": "audio/flac", ".webm": "audio/webm", ".mp4": "audio/mp4",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".md": "text/markdown",
}


def sanitize_filename(name: str) -> str:
    """Remove caminhos e caracteres perigosos; mantém extensão."""
    base = Path(name or "").name.replace("\x00", "")
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "arquivo"
    return base[:150]


def extension_of(name: str) -> str:
    return Path(name or "").suffix.lower()


@dataclass
class StoredFile:
    original_name: str
    path: Path
    size_bytes: int
    media_type: str


class FileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def save(
        self,
        project_id: int,
        filename: str,
        stream: BinaryIO,
        allowed: tuple[str, ...],
        max_bytes: int,
    ) -> StoredFile:
        """Valida e grava em streaming. Lança BusinessValidationError com o motivo da rejeição."""
        ext = extension_of(filename)
        if ext not in allowed:
            raise BusinessValidationError(
                f"Formato de arquivo não suportado: '{ext or 'sem extensão'}'. "
                f"Envie um dos formatos: {', '.join(allowed)}.",
                {"extensoes_aceitas": list(allowed)},
            )
        head = stream.read(4096)
        if not head:
            raise BusinessValidationError("O arquivo enviado está vazio.")
        if not SIGNATURES[ext](head):
            raise BusinessValidationError(
                f"O conteúdo do arquivo não corresponde ao formato {ext}. "
                "Verifique se o arquivo não está corrompido ou com a extensão errada."
            )
        target_dir = self.root / f"projeto_{project_id}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex}_{sanitize_filename(filename)}"
        size = 0
        try:
            with target.open("wb") as out:
                chunk = head
                while chunk:
                    size += len(chunk)
                    if size > max_bytes:
                        raise BusinessValidationError(
                            f"Arquivo excede o tamanho máximo de {max_bytes // (1024 * 1024)} MB.",
                            {"tamanho_maximo_bytes": max_bytes},
                        )
                    out.write(chunk)
                    chunk = stream.read(CHUNK_BYTES)
        except BusinessValidationError:
            target.unlink(missing_ok=True)
            raise
        return StoredFile(Path(filename).name or "arquivo", target, size, MEDIA_TYPES[ext])

    def resolve(self, stored_path: str) -> Path:
        """Resolve um caminho salvo garantindo que esteja dentro do diretório de uploads."""
        path = Path(stored_path).resolve()
        if self.root not in path.parents:
            raise BusinessValidationError("Caminho de arquivo inválido.")
        return path

    def remove(self, stored_path: str | None) -> None:
        if not stored_path:
            return
        try:
            self.resolve(stored_path).unlink(missing_ok=True)
        except BusinessValidationError:
            pass  # caminho fora da raiz nunca é removido
