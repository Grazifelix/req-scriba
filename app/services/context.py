"""Passada de contexto prévia: resumo estruturado e rastreável das fontes completas.

O LLM lê todas as fontes em lotes e lista perfis, objetivos e termos, sempre citando trechos (F1..Fn).
Os itens são agregados pelo código, recebem rótulos R1..Rn e entram em todos os prompts de extração.
Quando uma história cita um item R, a evidência é o trecho real resolvido aqui (nunca texto do LLM).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.container import Container
from app.core.errors import AppError
from app.domain.text import similarity
from app.llm import parsing, prompts
from app.services.retrieval import group_by_chars, label_chunks

logger = logging.getLogger(__name__)

SIMILAR_ITEM_THRESHOLD = 0.85
MAX_CHUNKS_PER_ITEM = 3
TYPE_PRIORITY = {"perfil": 0, "objetivo": 1, "termo": 2}  # perfil e benefício completam o template


@dataclass
class ContextItem:
    rotulo: str
    tipo: str
    texto: str
    chunks: list[dict]

    def line(self) -> str:
        return f"[{self.rotulo}] ({self.tipo}) {self.texto}"


@dataclass
class MeetingContext:
    items: list[ContextItem]
    batches: int = 0
    failures: list[dict] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(item.line() for item in self.items)

    def chunk_map(self) -> dict[str, list[dict]]:
        return {item.rotulo: item.chunks for item in self.items}

    def summary(self) -> dict[str, Any]:
        return {"itens": len(self.items), "lotes": self.batches, "lotes_com_erro": self.failures}


def _aggregate(raw: list[dict], limit_chars: int) -> list[ContextItem]:
    """Mescla itens parecidos do mesmo tipo, prioriza perfis/objetivos e respeita o limite de caracteres."""
    merged: list[dict] = []
    for item in raw:
        match = next(
            (m for m in merged if m["tipo"] == item["tipo"]
             and similarity(m["texto"], item["texto"]) >= SIMILAR_ITEM_THRESHOLD),
            None,
        )
        if match:
            known = {c["id"] for c in match["chunks"]}
            match["chunks"] += [c for c in item["chunks"] if c["id"] not in known]
        else:
            merged.append({**item, "chunks": list(item["chunks"])})
    merged.sort(key=lambda m: TYPE_PRIORITY.get(m["tipo"], len(TYPE_PRIORITY)))
    items: list[ContextItem] = []
    size = 0
    for entry in merged:
        item = ContextItem(f"R{len(items) + 1}", entry["tipo"], entry["texto"], entry["chunks"][:MAX_CHUNKS_PER_ITEM])
        if size + len(item.line()) + 1 > limit_chars:
            continue  # itens menores seguintes ainda podem caber
        items.append(item)
        size += len(item.line()) + 1
    return items


def build_context(container: Container, chunks: list[dict]) -> MeetingContext | None:
    """Retorna o contexto das fontes, ou None se a passada estiver desligada. Falhas nunca derrubam o job."""
    settings = container.settings
    if not settings.generation_context_pass or not chunks:
        return None
    batches = group_by_chars(chunks, settings.generation_batch_chars)
    raw, failures = [], []
    for index, batch in enumerate(batches, start=1):
        blocks, mapping = label_chunks(batch)
        try:
            data = container.llm.generate_json(prompts.SYSTEM, prompts.context_prompt(blocks))
        except AppError as exc:
            logger.warning("Lote %d da passada de contexto falhou: %s", index, exc.message)
            failures.append({"lote": index, "erro": exc.message})
            continue
        for item in parsing.normalize_context(data, set(mapping)):
            raw.append({"tipo": item["tipo"], "texto": item["texto"], "chunks": [mapping[l] for l in item["fontes"]]})
    return MeetingContext(_aggregate(raw, settings.generation_context_chars), len(batches), failures)
