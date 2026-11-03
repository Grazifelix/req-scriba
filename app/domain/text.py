"""Normalização e similaridade de textos (puro, sem dependências externas)."""

from __future__ import annotations

import difflib
import re
import unicodedata

_STOPWORDS = {
    "como", "quero", "para", "que", "um", "uma", "o", "a", "os", "as", "de", "do", "da", "dos",
    "das", "e", "em", "no", "na", "nos", "nas", "por", "com", "eu", "meu", "minha", "ser", "poder",
}


def normalize(text: str | None) -> str:
    """Minúsculas, sem acentos, sem pontuação, espaços colapsados."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    no_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", no_accents.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in _STOPWORDS]


def similarity(a: str | None, b: str | None) -> float:
    """Similaridade 0–1 combinando sequência (difflib) e sobreposição de termos (Jaccard)."""
    ta, tb = _tokens(a or ""), _tokens(b or "")
    if not ta or not tb:
        return 0.0
    seq = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    return round(max(seq, jaccard), 4)


def contains_normalized(haystack: str, needle: str) -> bool:
    """Busca ignorando acentos e maiúsculas."""
    n = normalize(needle)
    return bool(n) and n in normalize(haystack)


def unified_diff(current: str, proposed: str) -> str:
    """Diff textual (palavras por linha) entre conteúdo atual e proposto."""
    diff = difflib.unified_diff(
        (current or "").split(), (proposed or "").split(), "atual", "proposto", lineterm="", n=3
    )
    return "\n".join(diff)
