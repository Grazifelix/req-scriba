"""Recuperação semântica (RAG) sobre os trechos indexados e montagem de blocos para prompts."""

from __future__ import annotations

from typing import Any

from app.core.container import Container
from app.core.errors import BusinessValidationError, NotFoundError
from app.core.time import format_seconds
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import SourceType
from app.integrations.vector_store import build_where
from app.services import presenters


def _chunk_id(vector_id: str) -> int | None:
    try:
        return int(vector_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def related_chunks(
    uow: UnitOfWork,
    container: Container,
    project_id: int,
    query_text: str,
    top_k: int,
    source_keys: list[str] | None = None,
) -> list[tuple[dict, float]]:
    """Retorna [(trecho, relevância 0–1)] mais similares a ``query_text``."""
    if not query_text.strip():
        return []
    embedding = container.embedder.embed([query_text[:4000]])[0]
    hits = container.vector_store.query(embedding, top_k, build_where(project_id, source_keys))
    ids = [cid for cid in (_chunk_id(h["id"]) for h in hits) if cid is not None]
    chunks = {c["id"]: c for c in uow.chunks.list_by_ids(ids)}
    results = []
    for hit in hits:
        cid = _chunk_id(hit["id"])
        if cid in chunks:  # ignora vetores órfãos (sem linha no SQLite)
            results.append((chunks[cid], max(0.0, 1.0 - float(hit["distancia"]))))
    return results


def search(uow: UnitOfWork, container: Container, project_id: int, term: str, limit: int) -> dict[str, Any]:
    if not uow.projects.get(project_id):
        raise NotFoundError.for_entity("Projeto", project_id)
    if len(term.strip()) < 2:
        raise BusinessValidationError("O termo de busca deve ter ao menos 2 caracteres.")
    hits = related_chunks(uow, container, project_id, term, limit)
    return {"termo": term, **presenters.listing([presenters.chunk(c, score) for c, score in hits])}


def describe_chunk(chunk: dict) -> str:
    loc = chunk["localizacao"]
    if chunk["tipo_fonte"] == SourceType.AUDIO.value:
        return (
            f"áudio {chunk['nome_arquivo']}, {format_seconds(loc.get('inicio_segundos'))}"
            f"–{format_seconds(loc.get('fim_segundos'))}"
        )
    page = f", página {loc['pagina']}" if loc.get("pagina") else ""
    return f"documento {chunk['nome_arquivo']}{page}"


def label_chunks(chunks: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Rotula trechos como F1..Fn. Retorna (blocos para o prompt, mapa rótulo → trecho)."""
    blocks, mapping = [], {}
    for i, chunk in enumerate(chunks, start=1):
        label = f"F{i}"
        mapping[label] = chunk
        blocks.append({"rotulo": label, "descricao": describe_chunk(chunk), "texto": chunk["texto"]})
    return blocks, mapping


def group_by_chars(chunks: list[dict], budget: int) -> list[list[dict]]:
    """Agrupa trechos consecutivos em lotes que cabem no orçamento de caracteres."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for chunk in chunks:
        length = len(chunk["texto"])
        if current and size + length > budget:
            groups.append(current)
            current, size = [], 0
        current.append(chunk)
        size += length
    if current:
        groups.append(current)
    return groups
