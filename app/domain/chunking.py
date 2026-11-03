"""Chunking de fontes.

* Documentos: divisão recursiva por fronteiras estruturais (\\n\\n → \\n → ". " → espaço), com
  sobreposição e offsets exatos de caractere, página a página.
* Transcrições: janelas de segmentos consecutivos sem cortar segmentos (preserva os tempos).
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", " ")


@dataclass(frozen=True)
class TextChunk:
    text: str
    start: int  # offset inclusivo no texto original
    end: int  # offset exclusivo


@dataclass
class SegmentWindow:
    text: str
    start_seconds: float
    end_seconds: float
    segment_ids: list[int] = field(default_factory=list)


def _find_break(text: str, start: int, hard_end: int) -> int:
    """Melhor ponto de corte na segunda metade da janela, priorizando separadores estruturais."""
    min_pos = start + (hard_end - start) // 2
    for sep in SEPARATORS:
        pos = text.rfind(sep, min_pos, hard_end)
        if pos != -1:
            return pos + len(sep)
    return hard_end


def split_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[TextChunk]:
    """Divide ``text`` em trechos de até ``chunk_size`` caracteres com ``overlap``."""
    if chunk_size <= 0:
        raise ValueError("chunk_size deve ser positivo")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap deve estar em [0, chunk_size)")
    chunks: list[TextChunk] = []
    length = len(text)
    start = 0
    while start < length:
        while start < length and text[start].isspace():
            start += 1
        if start >= length:
            break
        hard_end = min(start + chunk_size, length)
        end = hard_end if hard_end == length else _find_break(text, start, hard_end)
        piece = text[start:end]
        stripped = piece.rstrip()
        if stripped:
            chunks.append(TextChunk(stripped, start, start + len(stripped)))
        if end >= length:
            break
        next_start = max(end - overlap, start + 1)
        # avança até o início de uma palavra para não começar no meio dela
        while 0 < next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start
    return chunks


def window_segments(segments: list[dict], max_chars: int = 800) -> list[SegmentWindow]:
    """Agrupa segmentos consecutivos (dicts com id, texto, inicio_segundos, fim_segundos)."""
    windows: list[SegmentWindow] = []
    current: SegmentWindow | None = None
    for seg in segments:
        text = (seg.get("texto") or "").strip()
        if not text:
            continue
        if current and len(current.text) + 1 + len(text) > max_chars:
            windows.append(current)
            current = None
        if current is None:
            current = SegmentWindow(text, float(seg["inicio_segundos"]), float(seg["fim_segundos"]), [seg["id"]])
        else:
            current.text = f"{current.text} {text}"
            current.end_seconds = float(seg["fim_segundos"])
            current.segment_ids.append(seg["id"])
    if current:
        windows.append(current)
    return windows
