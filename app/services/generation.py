"""Geração automática de Histórias de Usuário a partir das fontes selecionadas (US-05, US-16).

Pré-requisito: uma análise das fontes aprovada pelo analista (app.services.source_analysis). Ela entra no
prompt como ANALISE DO PROJETO, logo após as fontes, e as histórias citam os códigos FN dela. A análise aprovada
é a visão do todo de cada lote (usuários, requisitos de negócio, funcionalidades, termos): por isso a geração
não faz passada de contexto.
Modo chamada única (as fontes cabem em GENERATION_BATCH_CHARS): o LLM lê todas as fontes de uma vez e escreve
as histórias; não há consolidação.
Modo lotes (fontes maiores): lotes (transcrição + RAG de documentos) → extração por lote → deduplicação →
consolidação. Nos dois modos: descarte do que já existe → HU + evidências + v1 → histórias ligadas às
funcionalidades da análise.
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
from app.services.analysis import analysis_text, approved_analysis, functionality_codes, link_stories
from app.services.candidates import (
    DUPLICATE_THRESHOLD, EXISTING_DUPLICATE_THRESHOLD, deduplicate, extract_batch, extract_candidates, story_key,
)
from app.services.consolidation import consolidate
from app.services.context import MeetingContext
from app.services.indexing import source_key
from app.services.jobs import JobService, validate_sources
from app.services.retrieval import group_by_chars, related_chunks
from app.services.stories import create_story_record

logger = logging.getLogger(__name__)

__all__ = [
    "DUPLICATE_THRESHOLD", "EXISTING_DUPLICATE_THRESHOLD", "batch_budget", "build_batches", "deduplicate",
    "drop_existing", "extract_candidates", "fits_in_one_call", "run_generation", "start_generation", "story_key",
]


def start_generation(
    uow: UnitOfWork, project_id: int, audio_ids: list[int], document_ids: list[int], author: str,
    analysis_id: int | None = None,
) -> dict:
    """Cria o job. Exige análise aprovada (a informada ou a aprovada mais recente), fixada nos parâmetros do job."""
    validate_sources(uow, project_id, audio_ids, document_ids)
    analysis = approved_analysis(uow, project_id, analysis_id)
    job = JobService(uow).create(
        project_id, JobType.STORY_GENERATION,
        {"audio_ids": audio_ids, "documento_ids": document_ids, "analise_id": analysis["id"], "responsavel": author},
    )
    return presenters.job(job)


def batch_budget(container: Container, context: MeetingContext | None, analysis: str | None = None) -> int:
    """Orçamento de caracteres por lote, descontando contexto e texto da análise (piso: metade do orçamento)."""
    budget = container.settings.generation_batch_chars
    extra = len(analysis or "") + (len(context.text()) if context and context.items else 0)
    if not extra:
        return budget
    return max(budget - extra, budget // 2)


def fits_in_one_call(container: Container, chunks: list[dict], analysis: str | None = None) -> bool:
    """True quando todas as fontes (e o texto da análise) cabem em uma única chamada ao LLM."""
    total = sum(len(c["texto"]) for c in chunks) + len(analysis or "")
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
    container: Container, job_id: str, batches: list[list[dict]], analysis: str | None, fn_codes: set[str],
) -> tuple[list[dict], list[dict]]:
    """Extrai cada lote; as candidatas guardam o código FN da análise que citaram (só códigos existentes)."""
    candidates, failures = [], []
    complete = len(batches) == 1
    for index, batch in enumerate(batches, start=1):
        try:
            extracted = extract_batch(container, batch, None, analysis, complete)
        except AppError as exc:
            logger.warning("Lote %d do job %s falhou: %s", index, job_id, exc.message)
            failures.append({"lote": index, "erro": exc.message})
            continue
        for candidate in extracted:
            ref = candidate.pop("funcionalidade_ref", None)
            candidate["funcionalidades"] = [ref] if ref in fn_codes else []
        candidates.extend(extracted)
    if batches and len(failures) == len(batches):
        raise AppError("Todos os lotes falharam na extração.", {"lotes_com_erro": failures})
    return candidates, failures


def _prepare(container: Container, project_id: int, params: dict) -> tuple:
    """Lê fontes, histórias existentes e a análise aprovada do job; decide o modo; monta os lotes."""
    audio_ids, document_ids = params["audio_ids"], params["documento_ids"]
    with container.uow() as uow:
        validate_sources(uow, project_id, audio_ids, document_ids)
        analysis = approved_analysis(uow, project_id, params.get("analise_id"))
        chunks = source_chunks(uow, audio_ids, document_ids)
        existing = uow.stories.list_active(project_id)
        text, fn_codes = analysis_text(uow, analysis, for_stories=True), functionality_codes(uow, analysis["id"])
    if fits_in_one_call(container, chunks, text):
        return [chunks], existing, (analysis, text, fn_codes)
    with container.uow() as uow:
        budget = batch_budget(container, None, text)
        batches = build_batches(uow, container, project_id, audio_ids, document_ids, budget)
    return batches, existing, (analysis, text, fn_codes)


def run_generation(container: Container, job_id: str) -> None:
    """Tarefa de background do job de geração (fases curtas de banco + chamadas ao LLM fora da transação)."""
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    try:
        batches, existing, (analysis, text, fn_codes) = _prepare(container, project_id, params)
        single = len(batches) == 1
        candidates, failures = _extract_all(container, job_id, batches, text, fn_codes)
        candidates, consolidation = deduplicate(candidates), None
        if not single:  # na chamada única o modelo já viu o conjunto inteiro
            candidates, consolidation = consolidate(container, candidates)
        candidates, skipped = drop_existing(candidates, existing)
        with container.uow() as uow:
            created, links = [], []
            for c in candidates:
                story_id = create_story_record(
                    uow, project_id, c, origin="ia", change_type=ChangeType.AUTOMATIC,
                    change_origin=ChangeOrigin.AI_GENERATION, author="sistema", evidence_specs=c["evidencias"],
                )
                links.append((c.get("funcionalidades") or [], story_id))
                created.append(story_id)
            link_stories(uow, analysis["id"], links)
            result: dict[str, Any] = {
                "historias_criadas": created, "total": len(created), "lotes": len(batches),
                "modo": "chamada_unica" if single else "lotes", "lotes_com_erro": failures,
                "ignoradas_por_duplicidade": skipped, "analise_id": analysis["id"], "consolidacao": consolidation,
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
