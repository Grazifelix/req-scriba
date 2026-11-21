"""Geração automática de Histórias de Usuário a partir das fontes selecionadas (US-05, US-16).

Modo chamada única (as fontes cabem em GENERATION_BATCH_CHARS): o LLM lê todas as fontes de uma vez, entende
a reunião (análise) e escreve as histórias; não há passada de contexto nem consolidação.
Modo lotes (fontes maiores): contexto prévio → lotes (transcrição + RAG de documentos) → extração por lote →
deduplicação → consolidação. Nos dois modos: descarte do que já existe → HU + evidências + v1 → análise gravada.
A análise revisada mais recente do projeto entra no prompt como contexto confirmado pelo analista.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Container
from app.core.errors import AppError
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import ChangeOrigin, ChangeType, JobStatus, JobType, SourceType
from app.domain.text import similarity
from app.services import presenters
from app.services.analysis import AnalysisBuilder, reviewed_analysis_text, save_generated_analysis
from app.services.candidates import (
    DUPLICATE_THRESHOLD, EXISTING_DUPLICATE_THRESHOLD, deduplicate, extract_batch, extract_candidates, story_key,
)
from app.services.consolidation import consolidate
from app.services.context import MeetingContext, build_context
from app.services.indexing import source_key
from app.services.jobs import JobService, validate_sources
from app.services.retrieval import group_by_chars, related_chunks
from app.services.stories import create_story_record

logger = logging.getLogger(__name__)

__all__ = [
    "DUPLICATE_THRESHOLD", "EXISTING_DUPLICATE_THRESHOLD", "batch_budget", "build_batches", "deduplicate",
    "drop_existing", "extract_candidates", "fits_in_one_call", "run_generation", "start_generation", "story_key",
]


def start_generation(uow: UnitOfWork, project_id: int, audio_ids: list[int], document_ids: list[int], author: str) -> dict:
    validate_sources(uow, project_id, audio_ids, document_ids)
    job = JobService(uow).create(
        project_id, JobType.STORY_GENERATION,
        {"audio_ids": audio_ids, "documento_ids": document_ids, "responsavel": author},
    )
    return presenters.job(job)


def batch_budget(container: Container, context: MeetingContext | None, reviewed_text: str | None = None) -> int:
    """Orçamento de caracteres por lote, descontando contexto e análise revisada (piso: metade do orçamento)."""
    budget = container.settings.generation_batch_chars
    extra = len(reviewed_text or "") + (len(context.text()) if context and context.items else 0)
    if not extra:
        return budget
    return max(budget - extra, budget // 2)


def fits_in_one_call(container: Container, chunks: list[dict], reviewed_text: str | None = None) -> bool:
    """True quando todas as fontes (e a análise revisada) cabem em uma única chamada ao LLM."""
    total = sum(len(c["texto"]) for c in chunks) + len(reviewed_text or "")
    return total <= container.settings.generation_batch_chars


def source_chunks(uow: UnitOfWork, audio_ids: list[int], document_ids: list[int]) -> list[dict]:
    chunks = [c for a in audio_ids for c in uow.chunks.list_by_source(SourceType.AUDIO.value, a)]
    return chunks + [c for d in document_ids for c in uow.chunks.list_by_source(SourceType.DOCUMENT.value, d)]


def build_batches(
    uow: UnitOfWork, container: Container, project_id: int, audio_ids: list[int], document_ids: list[int],
    budget: int | None = None,
) -> list[list[dict]]:
    """Lotes de trechos: transcrição + documentos relacionados (RAG); depois documentos restantes."""
    settings = container.settings
    budget = budget or settings.generation_batch_chars
    audio_chunks = [c for a in audio_ids for c in uow.chunks.list_by_source(SourceType.AUDIO.value, a)]
    doc_chunks = [c for d in document_ids for c in uow.chunks.list_by_source(SourceType.DOCUMENT.value, d)]
    doc_keys = [source_key(SourceType.DOCUMENT.value, d) for d in document_ids]
    batches, used_docs = [], set()
    transcript_budget = int(budget * 0.7) if doc_chunks else budget
    for group in group_by_chars(audio_chunks, transcript_budget):
        batch = list(group)
        if doc_keys:
            query = " ".join(c["texto"] for c in group)
            for chunk, _score in related_chunks(uow, container, project_id, query, settings.rag_top_k, doc_keys):
                if chunk["id"] not in used_docs:
                    batch.append(chunk)
                    used_docs.add(chunk["id"])
        batches.append(batch)
    remaining = [c for c in doc_chunks if c["id"] not in used_docs]
    batches.extend(group_by_chars(remaining, budget))
    return batches


def drop_existing(candidates: list[dict], existing: list[dict]) -> tuple[list[dict], list[str]]:
    kept, skipped = [], []
    for candidate in candidates:
        duplicate = next(
            (s for s in existing if similarity(f"{s['perfil'] or ''} {s['funcionalidade'] or ''}", story_key(candidate))
             >= EXISTING_DUPLICATE_THRESHOLD),
            None,
        )
        if duplicate:
            skipped.append(duplicate["codigo"])
        else:
            kept.append(candidate)
    return kept, skipped


def _extract_all(
    container: Container, job_id: str, batches: list[list[dict]], context: MeetingContext | None,
    reviewed_text: str | None, builder: AnalysisBuilder,
) -> tuple[list[dict], list[dict]]:
    """Extrai cada lote; a análise de cada lote entra no builder e as candidatas recebem os códigos FN globais."""
    candidates, failures = [], []
    complete = len(batches) == 1
    for index, batch in enumerate(batches, start=1):
        try:
            extraction = extract_batch(container, batch, context, reviewed_text, complete)
        except AppError as exc:
            logger.warning("Lote %d do job %s falhou: %s", index, job_id, exc.message)
            failures.append({"lote": index, "erro": exc.message})
            continue
        code_map = builder.add(extraction.analysis, extraction.sources)
        for candidate in extraction.candidates:
            ref = candidate.pop("funcionalidade_ref", None)
            candidate["funcionalidades"] = [code_map[ref]] if ref in code_map else []
        candidates.extend(extraction.candidates)
    if batches and len(failures) == len(batches):
        raise AppError("Todos os lotes falharam na extração.", {"lotes_com_erro": failures})
    return candidates, failures


def _prepare(container: Container, project_id: int, audio_ids: list[int], document_ids: list[int]) -> tuple:
    """Lê fontes, histórias existentes e análise revisada; decide o modo; monta os lotes."""
    with container.uow() as uow:
        validate_sources(uow, project_id, audio_ids, document_ids)
        chunks = source_chunks(uow, audio_ids, document_ids)
        existing = uow.stories.list_active(project_id)
        reviewed = reviewed_analysis_text(uow, project_id)
    if fits_in_one_call(container, chunks, reviewed):
        return [chunks], None, existing, reviewed
    context = build_context(container, chunks)
    with container.uow() as uow:
        budget = batch_budget(container, context, reviewed)
        batches = build_batches(uow, container, project_id, audio_ids, document_ids, budget)
    return batches, context, existing, reviewed


def run_generation(container: Container, job_id: str) -> None:
    """Tarefa de background do job de geração (fases curtas de banco + chamadas ao LLM fora da transação)."""
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    audio_ids, document_ids = params["audio_ids"], params["documento_ids"]
    try:
        batches, context, existing, reviewed = _prepare(container, project_id, audio_ids, document_ids)
        single = len(batches) == 1
        builder = AnalysisBuilder()
        candidates, failures = _extract_all(container, job_id, batches, context, reviewed, builder)
        candidates, consolidation = deduplicate(candidates), None
        if not single:  # na chamada única o modelo já viu o conjunto inteiro
            candidates, consolidation = consolidate(container, candidates)
        candidates, skipped = drop_existing(candidates, existing)
        with container.uow() as uow:
            created = []
            for c in candidates:
                story_id = create_story_record(
                    uow, project_id, c, origin="ia", change_type=ChangeType.AUTOMATIC,
                    change_origin=ChangeOrigin.AI_GENERATION, author="sistema", evidence_specs=c["evidencias"],
                )
                builder.link_story(c.get("funcionalidades") or [], story_id)
                created.append(story_id)
            analysis_id = save_generated_analysis(uow, project_id, job_id, builder, audio_ids, document_ids)
            result: dict[str, Any] = {
                "historias_criadas": created, "total": len(created), "lotes": len(batches),
                "modo": "chamada_unica" if single else "lotes", "lotes_com_erro": failures,
                "ignoradas_por_duplicidade": skipped, "analise_id": analysis_id,
                "analise_revisada_usada": reviewed is not None,
                "contexto": context.summary() if context else None, "consolidacao": consolidation,
                "mensagem": None if created else "Nenhuma nova necessidade funcional foi identificada nas fontes.",
            }
            JobService(uow).mark(job_id, JobStatus.COMPLETED, result=result)
    except AppError as exc:
        _fail(container, job_id, exc.message, exc.details)
    except Exception as exc:
        logger.exception("Falha inesperada no job %s", job_id)
        _fail(container, job_id, f"Erro inesperado: {type(exc).__name__}", None)


def _fail(container: Container, job_id: str, message: str, details: Any) -> None:
    with container.uow() as uow:
        JobService(uow).mark(job_id, JobStatus.ERROR, result={"detalhes": details} if details else None, error=message)
