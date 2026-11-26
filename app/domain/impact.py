"""Evolução incremental: recuperação de histórias potencialmente relacionadas a trechos novos (puro, sem I/O).

Similaridade NÃO é equivalência: os sinais abaixo só escolhem quais histórias o LLM vai comparar com os trechos
novos (ANALISE_IMPACTO_FONTES). Quem classifica o impacto é o modelo, sob as regras do código.

Sinais, do mais forte ao mais fraco:
- ``funcionalidade``: o trecho novo é evidência de uma funcionalidade (FN) da análise aprovada, e a FN registra
  as histórias geradas a partir dela (vínculo revisado pelo analista);
- ``termo``: um termo do domínio da análise aparece no trecho e na história;
- ``semantica``: a história está entre as mais próximas do trecho por embeddings. Usa a POSIÇÃO no ranking, não
  um limiar: num projeto do mesmo domínio a similaridade fica alta para qualquer par (D26).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.domain.text import normalize

SIGNAL_WEIGHTS = {"funcionalidade": 3.0, "termo": 1.0, "semantica": 1.0}
MIN_TERM_LENGTH = 3


@dataclass
class Candidate:
    story_id: int
    score: float = 0.0
    best_similarity: float = 0.0
    chunk_ids: set[int] = field(default_factory=set)
    signals: list[dict[str, Any]] = field(default_factory=list)

    def add(self, chunk_id: int, signal: str, detail: str, similarity: float = 0.0) -> None:
        self.chunk_ids.add(chunk_id)
        self.score += SIGNAL_WEIGHTS[signal]
        self.best_similarity = max(self.best_similarity, similarity)
        self.signals.append({"trecho_id": chunk_id, "sinal": signal, "detalhe": detail})


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def story_text(story: dict[str, Any]) -> str:
    """Texto da história usado na recuperação: frase, título e critérios."""
    criteria = " ".join(c.get("criterio") or "" for c in story.get("criterios_aceitacao") or [])
    return " ".join(p for p in (story.get("titulo"), story.get("historia"), criteria) if p)


def _contains_term(text: str, term: str) -> bool:
    return f" {term} " in f" {text} "


def find_candidates(
    chunks: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    chunk_vectors: dict[int, list[float]],
    story_vectors: dict[int, list[float]],
    chunk_functionalities: dict[int, set[str]],
    functionality_stories: dict[str, set[int]],
    terms: list[str],
    top_k: int,
) -> dict[int, Candidate]:
    """Histórias candidatas (id → sinais), a partir de cada trecho novo."""
    candidates: dict[int, Candidate] = {}
    known = {s["id"] for s in stories}
    texts = {s["id"]: normalize(story_text(s)) for s in stories}
    normalized_terms = [t for t in {normalize(t) for t in terms} if len(t) >= MIN_TERM_LENGTH]

    def candidate(story_id: int) -> Candidate:
        return candidates.setdefault(story_id, Candidate(story_id))

    for chunk in chunks:
        cid = chunk["id"]
        for code in sorted(chunk_functionalities.get(cid, set())):
            for story_id in sorted(functionality_stories.get(code, set()) & known):
                candidate(story_id).add(cid, "funcionalidade", code)
        chunk_text = normalize(chunk["texto"])
        for term in normalized_terms:
            if _contains_term(chunk_text, term):
                for story_id, text in texts.items():
                    if _contains_term(text, term):
                        candidate(story_id).add(cid, "termo", term)
        vector = chunk_vectors.get(cid)
        if vector is None or top_k <= 0:
            continue
        ranked = sorted(((cosine(vector, story_vectors[sid]), sid) for sid in known if sid in story_vectors),
                        reverse=True)[:top_k]
        for rank, (value, story_id) in enumerate(ranked, start=1):
            candidate(story_id).add(cid, "semantica", f"posição {rank} ({value:.2f})", value)
    return candidates


def strongest(candidates: list[Candidate], limit: int) -> tuple[list[Candidate], list[Candidate]]:
    """(as ``limit`` candidatas de maior pontuação, as que ficaram de fora). Empate: maior similaridade."""
    ordered = sorted(candidates, key=lambda c: (c.score, c.best_similarity), reverse=True)
    return ordered[:limit], ordered[limit:]
