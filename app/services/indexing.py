"""Criação de trechos (chunks) a partir das fontes e indexação no banco vetorial."""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Container
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.chunking import split_text, window_segments
from app.domain.enums import SourceType
from app.integrations.extractors import ExtractedPage

logger = logging.getLogger(__name__)

REINDEX_BATCH_SIZE = 64
REINDEX_PENDING = "reindexacao_pendente"


def vector_id(chunk_id: int) -> str:
    return f"trecho-{chunk_id}"


def source_key(source_type: str, source_id: int) -> str:
    return f"{source_type}:{source_id}"


def _vector_metadata(chunk: dict) -> dict:
    return {
        "projeto_id": chunk["projeto_id"], "tipo_fonte": chunk["tipo_fonte"], "fonte_id": chunk["fonte_id"],
        "fonte_chave": source_key(chunk["tipo_fonte"], chunk["fonte_id"]),
        "nome_arquivo": chunk["nome_arquivo"], "posicao": chunk["posicao"],
    }


def _store_chunks(
    uow: UnitOfWork,
    container: Container,
    *,
    project_id: int,
    source_type: str,
    source_id: int,
    filename: str,
    pieces: list[tuple[str, dict]],
) -> int:
    """Calcula embeddings, grava trechos no SQLite e no ChromaDB. Retorna a quantidade."""
    remove_source_chunks(uow, container, source_type, source_id)
    if not pieces:
        return 0
    embeddings = container.embedder.embed([text for text, _ in pieces])
    now = utc_now_iso()
    ids, texts, metadatas = [], [], []
    for position, (text, location) in enumerate(pieces):
        chunk = {
            "projeto_id": project_id, "tipo_fonte": source_type, "fonte_id": source_id,
            "nome_arquivo": filename, "posicao": position, "texto": text, "localizacao": location,
            "criado_em": now,
        }
        ids.append(vector_id(uow.chunks.insert(chunk)))
        texts.append(text)
        metadatas.append(_vector_metadata(chunk))
    container.vector_store.upsert(ids, texts, embeddings, metadatas)
    logger.info("Indexados %d trechos de %s", len(ids), source_key(source_type, source_id))
    return len(ids)


def index_audio(uow: UnitOfWork, container: Container, audio: dict) -> int:
    """(Re)constrói as janelas de transcrição do áudio a partir dos segmentos revisados."""
    segments = uow.segments.list_by_audio(audio["id"])
    windows = window_segments(segments, container.settings.transcript_window_chars)
    pieces = [
        (w.text, {"inicio_segundos": w.start_seconds, "fim_segundos": w.end_seconds, "segmento_ids": w.segment_ids})
        for w in windows
    ]
    return _store_chunks(
        uow, container, project_id=audio["projeto_id"], source_type=SourceType.AUDIO.value,
        source_id=audio["id"], filename=audio["nome_arquivo"], pieces=pieces,
    )


def index_document(uow: UnitOfWork, container: Container, document: dict, pages: list[ExtractedPage]) -> int:
    settings = container.settings
    pieces: list[tuple[str, dict]] = []
    for page in pages:
        for chunk in split_text(page.text, settings.chunk_size, settings.chunk_overlap):
            pieces.append((chunk.text, {
                "pagina": page.number, "inicio_caractere": chunk.start, "fim_caractere": chunk.end,
                "indice_trecho": len(pieces),
            }))
    return _store_chunks(
        uow, container, project_id=document["projeto_id"], source_type=SourceType.DOCUMENT.value,
        source_id=document["id"], filename=document["nome_arquivo"], pieces=pieces,
    )


def remove_source_chunks(uow: UnitOfWork, container: Container, source_type: str, source_id: int) -> None:
    removed = uow.chunks.delete_by_source(source_type, source_id)
    container.vector_store.delete([vector_id(i) for i in removed])


def reindex_all(container: Container, batch_size: int = REINDEX_BATCH_SIZE) -> int:
    """Recalcula os vetores de todos os trechos do SQLite com o modelo atual. Retorna a quantidade."""
    with container.uow() as uow:
        chunks = uow.chunks.list_all()
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        texts = [chunk["texto"] for chunk in batch]
        container.vector_store.upsert(
            [vector_id(chunk["id"]) for chunk in batch], texts, container.embedder.embed(texts),
            [_vector_metadata(chunk) for chunk in batch],
        )
        logger.info("Reindexados %d de %d trechos", start + len(batch), len(chunks))
    return len(chunks)


def vector_index_health(uow: UnitOfWork, container: Container) -> dict[str, Any]:
    """Saúde do banco vetorial; sinaliza trechos do SQLite sem vetor na coleção do modelo atual."""
    health = container.vector_store.health()
    if health.get("status") != "ok":
        return health
    pending = uow.chunks.count_all() > health.get("vetores", 0)
    return {**health, "status": REINDEX_PENDING if pending else "ok", REINDEX_PENDING: pending}
