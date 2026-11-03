"""Geração automática de Histórias de Usuário a partir das fontes selecionadas (US-05, US-16)."""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Container
from app.core.errors import AppError
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import ChangeOrigin, ChangeType, JobStatus, JobType, SourceType
from app.domain.text import similarity
from app.llm import parsing, prompts
from app.services import presenters
from app.services.evidence import evidence_spec
from app.services.indexing import source_key
from app.services.jobs import JobService, validate_sources
from app.services.retrieval import group_by_chars, label_chunks, related_chunks
from app.services.stories import create_story_record

logger = logging.getLogger(__name__)

DUPLICATE_THRESHOLD = 0.85
EXISTING_DUPLICATE_THRESHOLD = 0.90


def start_generation(uow: UnitOfWork, project_id: int, audio_ids: list[int], document_ids: list[int], author: str) -> dict:
    validate_sources(uow, project_id, audio_ids, document_ids)
    job = JobService(uow).create(
        project_id, JobType.STORY_GENERATION,
        {"audio_ids": audio_ids, "documento_ids": document_ids, "responsavel": author},
    )
    return presenters.job(job)


def story_key(candidate: dict) -> str:
    return f"{candidate.get('perfil') or ''} {candidate['funcionalidade']}"


def build_batches(uow: UnitOfWork, container: Container, project_id: int, audio_ids: list[int], document_ids: list[int]) -> list[list[dict]]:
    """Lotes de trechos: transcrição + documentos relacionados (RAG); depois documentos restantes."""
    settings = container.settings
    audio_chunks = [c for a in audio_ids for c in uow.chunks.list_by_source(SourceType.AUDIO.value, a)]
    doc_chunks = [c for d in document_ids for c in uow.chunks.list_by_source(SourceType.DOCUMENT.value, d)]
    doc_keys = [source_key(SourceType.DOCUMENT.value, d) for d in document_ids]
    batches, used_docs = [], set()
    transcript_budget = int(settings.generation_batch_chars * 0.7) if doc_chunks else settings.generation_batch_chars
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
    batches.extend(group_by_chars(remaining, settings.generation_batch_chars))
    return batches


def extract_candidates(container: Container, batch: list[dict]) -> list[dict]:
    """Chama o LLM para um lote e converte rótulos citados em especificações de evidência."""
    blocks, mapping = label_chunks(batch)
    data = container.llm.generate_json(prompts.SYSTEM, prompts.extraction_prompt(blocks))
    candidates = []
    for story in parsing.normalize_extraction(data, set(mapping)):
        story["evidencias"] = [evidence_spec(mapping[label]) for label in story.pop("fontes")]
        candidates.append(story)
    return candidates


def _merge(target: dict, other: dict) -> None:
    seen = {e["trecho_id"] for e in target["evidencias"]}
    target["evidencias"] += [e for e in other["evidencias"] if e["trecho_id"] not in seen]
    for field in ("perfil", "beneficio"):
        target[field] = target.get(field) or other.get(field)
    for criterion in other["criterios_aceitacao"]:
        if all(similarity(criterion["criterio"], c["criterio"]) < DUPLICATE_THRESHOLD for c in target["criterios_aceitacao"]):
            target["criterios_aceitacao"].append(criterion)
    target["criterios_aceitacao"] = target["criterios_aceitacao"][:8]


def deduplicate(candidates: list[dict]) -> list[dict]:
    """Mescla candidatas muito similares geradas em lotes diferentes."""
    unique: list[dict] = []
    for candidate in candidates:
        match = next((u for u in unique if similarity(story_key(u), story_key(candidate)) >= DUPLICATE_THRESHOLD), None)
        if match:
            _merge(match, candidate)
        else:
            unique.append(candidate)
    return unique


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


def run_generation(container: Container, job_id: str) -> None:
    """Tarefa de background do job de geração (fases curtas de banco + chamadas ao LLM fora da transação)."""
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    try:
        with container.uow() as uow:
            validate_sources(uow, project_id, params["audio_ids"], params["documento_ids"])
            batches = build_batches(uow, container, project_id, params["audio_ids"], params["documento_ids"])
            existing = uow.stories.list_active(project_id)
        candidates, failures = [], []
        for index, batch in enumerate(batches, start=1):
            try:
                candidates.extend(extract_candidates(container, batch))
            except AppError as exc:
                logger.warning("Lote %d do job %s falhou: %s", index, job_id, exc.message)
                failures.append({"lote": index, "erro": exc.message})
        if batches and len(failures) == len(batches):
            raise AppError("Todos os lotes falharam na extração.", {"lotes_com_erro": failures})
        candidates, skipped = drop_existing(deduplicate(candidates), existing)
        with container.uow() as uow:
            created = [
                create_story_record(
                    uow, project_id, c, origin="ia", change_type=ChangeType.AUTOMATIC,
                    change_origin=ChangeOrigin.AI_GENERATION, author="sistema", evidence_specs=c["evidencias"],
                )
                for c in candidates
            ]
            result: dict[str, Any] = {
                "historias_criadas": created, "total": len(created), "lotes": len(batches),
                "lotes_com_erro": failures, "ignoradas_por_duplicidade": skipped,
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
