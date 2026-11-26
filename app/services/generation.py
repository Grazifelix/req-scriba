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
from app.domain.traceability import CRITERIA_ELEMENT, criteria_report, story_report
from app.llm import prompts
from app.services.candidates import (
    DUPLICATE_THRESHOLD, EXISTING_DUPLICATE_THRESHOLD, deduplicate, extract_batch_result, extract_candidates, story_key,
)
from app.services.criteria import (
    CriteriaOutcome, CriteriaTarget, apply_to_candidate, evidence_chunks, generate_criteria, related_for,
)
from app.services.pipeline import StageLog
from app.services.consolidation import consolidate
from app.services.context import MeetingContext
from app.services.indexing import source_key
from app.services.jobs import JobService, validate_sources
from app.services.retrieval import group_by_chars, related_chunks
from app.services.stories import build_content, create_story_record

NOT_GENERATED = "Critérios de aceitação ainda não gerados para esta história."

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
) -> tuple[list[dict], list[dict], list[dict]]:
    """GERACAO_US_QUS em cada lote. Retorna (candidatas, falhas por lote, cobertura funcional declarada).

    As candidatas guardam o código FN da análise que citaram (só códigos existentes)."""
    candidates, failures, coverage = [], [], []
    complete = len(batches) == 1
    for index, batch in enumerate(batches, start=1):
        try:
            extracted = extract_batch_result(container, batch, None, analysis, complete)
        except AppError as exc:
            logger.warning("Lote %d do job %s falhou: %s", index, job_id, exc.message)
            failures.append({"lote": index, "erro": exc.message})
            continue
        for candidate in extracted["historias"]:
            ref = candidate.pop("funcionalidade_ref", None)
            candidate["funcionalidades"] = [ref] if ref in fn_codes else []
        candidates.extend(extracted["historias"])
        coverage += [{**c, "lote": index} for c in extracted["cobertura"] if c["funcionalidade_ref"] in fn_codes]
    if batches and len(failures) == len(batches):
        raise AppError("Todos os lotes falharam na extração.", {"lotes_com_erro": failures})
    return candidates, failures, coverage


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


def _selected_keys(params: dict) -> list[str]:
    return ([source_key(SourceType.AUDIO.value, a) for a in params["audio_ids"]]
            + [source_key(SourceType.DOCUMENT.value, d) for d in params["documento_ids"]])


def _criteria_stage(
    container: Container, project_id: int, candidates: list[dict], text: str | None, params: dict,
) -> dict[str, Any]:
    """GERACAO_CRITERIOS_ACEITACAO sobre as candidatas, antes de gravá-las: a versão 1 já nasce com os critérios.

    Uma história cujo lote falhou é gravada sem critérios e com a lacuna sinalizada (pode ser regerada depois)."""
    keys = _selected_keys(params)
    with container.uow() as uow:
        targets = []
        for index, candidate in enumerate(candidates, start=1):
            story = {**candidate, **build_content(candidate)}
            evidences = evidence_chunks(candidate["evidencias"])
            related = related_for(uow, container, project_id, story, {c["id"] for c in evidences}, keys)
            targets.append(CriteriaTarget(f"H{index}", story, evidences, related))
    outcomes, failures = generate_criteria(container, targets, text)
    for target, candidate in zip(targets, candidates):
        apply_to_candidate(candidate, outcomes.get(target.ref))
    if targets and len(failures) == len(targets):
        raise AppError("A geração de critérios falhou para todas as histórias.", {"falhas": failures})
    return {"historias": len(targets), "falhas": failures}


def _without_criteria(candidates: list[dict], reason: str) -> None:
    for candidate in candidates:
        apply_to_candidate(candidate, CriteriaOutcome([], [{"elemento": CRITERIA_ELEMENT, "descricao": reason}], []))


def _persist(container: Container, project_id: int, candidates: list[dict], analysis: dict) -> list[int]:
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
    return created


def _criteria_step(container: Container, stages: StageLog, project_id: int, candidates: list[dict],
                   text: str | None, params: dict) -> list[dict]:
    """Etapa de critérios (não fatal). Retorna as falhas registradas."""
    if not candidates:
        stages.skip("geracao_criterios", "nenhuma história nova")
        return []
    if not container.settings.generation_criteria:
        stages.skip("geracao_criterios", "desligada (REQSCRIBA_GENERATION_CRITERIA)")
        _without_criteria(candidates, NOT_GENERATED)
        return []
    info: dict[str, Any] = {"falhas": []}
    with stages.run("geracao_criterios", fatal=False) as stage:
        info = _criteria_stage(container, project_id, candidates, text, params)
        stage.update(historias=info["historias"], falhas=len(info["falhas"]))
    if stages.failed("geracao_criterios"):
        return [{"erro": stages.stages[-1]["erro"]}]
    return info["falhas"]


def run_generation(container: Container, job_id: str) -> None:
    """Job de geração: preparação → GERACAO_US_QUS → consolidação das US → validação estrutural das US →
    GERACAO_CRITERIOS_ACEITACAO → validação estrutural dos critérios → persistência.

    Cada etapa registra status e duração em ``resultado.etapas``. A falha dos critérios não perde as histórias:
    elas são gravadas sem critérios e com a lacuna sinalizada. Chamadas ao LLM ficam fora das transações."""
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    stages = StageLog()
    try:
        with stages.run("preparacao") as stage:
            batches, existing, (analysis, text, fn_codes) = _prepare(container, project_id, params)
            single = len(batches) == 1
            stage.update(lotes=len(batches), modo="chamada_unica" if single else "lotes")
        with stages.run("geracao_us") as stage:
            candidates, failures, coverage = _extract_all(container, job_id, batches, text, fn_codes)
            stage.update(candidatas=len(candidates), lotes_com_erro=len(failures))
        with stages.run("consolidacao_us") as stage:
            candidates, consolidation = deduplicate(candidates), None
            if not single:  # na chamada única o modelo já viu o conjunto inteiro
                candidates, consolidation = consolidate(container, candidates)
            candidates, skipped = drop_existing(candidates, existing)
            stage.update(historias=len(candidates), ignoradas_por_duplicidade=len(skipped))
        with stages.run("validacao_us") as stage:
            story_validation = story_report(candidates)
            stage.update(story_validation)
        criteria_failures = _criteria_step(container, stages, project_id, candidates, text, params)
        with stages.run("validacao_criterios") as stage:
            criteria_validation = criteria_report(candidates)
            stage.update(criteria_validation)
        with stages.run("persistencia") as stage:
            created = _persist(container, project_id, candidates, analysis)
            stage.update(historias_criadas=len(created))
        result: dict[str, Any] = {
            "historias_criadas": created, "total": len(created), "lotes": len(batches),
            "modo": "chamada_unica" if single else "lotes", "lotes_com_erro": failures,
            "ignoradas_por_duplicidade": skipped, "analise_id": analysis["id"], "consolidacao": consolidation,
            "cobertura_funcional": coverage,
            "validacao": {"historias": story_validation, "criterios": criteria_validation},
            "criterios": {"falhas": criteria_failures},
            "etapas": stages.stages, "duracao_total_s": stages.total_seconds(),
            "versoes_prompt": prompts.prompt_versions(),
            "mensagem": None if created else "Nenhuma nova necessidade funcional foi identificada nas fontes.",
        }
        with container.uow() as uow:
            JobService(uow).mark(job_id, JobStatus.COMPLETED, result=result)
    except AppError as exc:
        details = {"detalhes": exc.details} if exc.details else {}
        _fail(container, job_id, exc.message, {**details, "etapas": stages.stages})
    except Exception as exc:
        logger.exception("Falha inesperada no job %s", job_id)
        _fail(container, job_id, f"Erro inesperado: {type(exc).__name__}", {"etapas": stages.stages})


def _fail(container: Container, job_id: str, message: str, result: dict | None) -> None:
    with container.uow() as uow:
        JobService(uow).mark(job_id, JobStatus.ERROR, result=result or None, error=message)
