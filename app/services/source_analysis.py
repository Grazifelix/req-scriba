"""Análise das fontes (etapa anterior à geração de histórias), em job próprio e reutilizável.

Lê as fontes informadas e a análise mais recente do projeto, pede ao LLM a análise atualizada (sistema,
participantes, usuários, requisitos de negócio, funcionalidades, fora de escopo) e grava uma NOVA análise não
revisada. Itens, códigos RN/FN e edições da análise anterior são preservados. O analista revisa e aprova a
análise antes de gerar histórias (``app.services.generation``).
Chamada única quando as fontes cabem em GENERATION_BATCH_CHARS; senão, contexto prévio + lotes mesclados.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Container
from app.core.errors import AppError
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import JobStatus, JobType
from app.llm import parsing, prompts
from app.services import presenters
from app.services.analysis import AnalysisBuilder, analysis_text, save_generated_analysis
from app.services.candidates import labeled_sources
from app.services.context import MeetingContext, build_context
from app.services.generation import batch_budget, fits_in_one_call, source_chunks
from app.services.jobs import JobService, validate_sources
from app.services.retrieval import group_by_chars

logger = logging.getLogger(__name__)


def start_source_analysis(
    uow: UnitOfWork, project_id: int, audio_ids: list[int], document_ids: list[int], author: str
) -> dict:
    validate_sources(uow, project_id, audio_ids, document_ids)
    job = JobService(uow).create(
        project_id, JobType.SOURCE_ANALYSIS,
        {"audio_ids": audio_ids, "documento_ids": document_ids, "responsavel": author},
    )
    return presenters.job(job)


def analyze_batch(
    container: Container, batch: list[dict], context: MeetingContext | None, previous_text: str | None,
    complete: bool,
) -> tuple[dict[str, Any], dict[str, list[dict]]]:
    """Chama o LLM de análise para um lote. Retorna (análise normalizada, rótulo → trechos)."""
    blocks, sources, context_text = labeled_sources(batch, context)
    prompt = prompts.analysis_prompt(blocks, context_text, previous_text, complete)
    raw = container.llm.generate_json(prompts.SYSTEM, prompt)
    return parsing.normalize_analysis_result(raw, set(sources)), sources


def _prepare(container: Container, project_id: int, params: dict) -> tuple:
    """Lê fontes e a análise anterior; decide o modo e monta os lotes."""
    with container.uow() as uow:
        validate_sources(uow, project_id, params["audio_ids"], params["documento_ids"])
        chunks = source_chunks(uow, params["audio_ids"], params["documento_ids"])
        previous = uow.analyses.latest(project_id)
        previous_items = uow.analysis_items.list_by_analysis(previous["id"]) if previous else []
        previous_text = analysis_text(uow, previous)
    if fits_in_one_call(container, chunks, previous_text):
        return [chunks], None, previous, previous_items, previous_text
    context = build_context(container, chunks)
    batches = group_by_chars(chunks, batch_budget(container, context, previous_text))
    return batches, context, previous, previous_items, previous_text


def run_source_analysis(container: Container, job_id: str) -> None:
    """Tarefa de background do job de análise das fontes."""
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    try:
        batches, context, previous, previous_items, previous_text = _prepare(container, project_id, params)
        builder = AnalysisBuilder()
        if previous:
            builder.seed(previous, previous_items)
        failures = []
        for index, batch in enumerate(batches, start=1):
            try:
                analysis, sources = analyze_batch(container, batch, context, previous_text, len(batches) == 1)
            except AppError as exc:
                logger.warning("Lote %d do job %s falhou: %s", index, job_id, exc.message)
                failures.append({"lote": index, "erro": exc.message})
                continue
            builder.add(analysis, sources)
        if batches and len(failures) == len(batches):
            raise AppError("Todos os lotes falharam na análise das fontes.", {"lotes_com_erro": failures})
        with container.uow() as uow:
            analysis_id = save_generated_analysis(
                uow, project_id, job_id, builder, params["audio_ids"], params["documento_ids"],
                previous["fontes"] if previous else None,
            )
            JobService(uow).mark(job_id, JobStatus.COMPLETED, result={
                "analise_id": analysis_id, "analise_anterior_id": previous["id"] if previous else None,
                "lotes": len(batches), "modo": "chamada_unica" if len(batches) == 1 else "lotes",
                "lotes_com_erro": failures, "contexto": context.summary() if context else None,
                "mensagem": None if analysis_id else "O modelo não identificou elementos de análise nas fontes.",
            })
    except AppError as exc:
        _fail(container, job_id, exc.message, exc.details)
    except Exception as exc:
        logger.exception("Falha inesperada no job %s", job_id)
        _fail(container, job_id, f"Erro inesperado: {type(exc).__name__}", None)


def _fail(container: Container, job_id: str, message: str, details: Any) -> None:
    with container.uow() as uow:
        JobService(uow).mark(job_id, JobStatus.ERROR, result={"detalhes": details} if details else None, error=message)
