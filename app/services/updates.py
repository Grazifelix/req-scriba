"""Atualização iterativa a partir de novas fontes (US-13, US-14, US-15, US-18)."""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Container
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import (
    ChangeOrigin, ChangeType, JobStatus, JobType, SourceType, SuggestionStatus, SuggestionType,
)
from app.domain.text import similarity
from app.llm import parsing, prompts
from app.services import presenters
from app.services.common import require_project
from app.services.evidence import add_evidences, evidence_spec
from app.services.generation import EXISTING_DUPLICATE_THRESHOLD, deduplicate, extract_candidates, story_key
from app.services.indexing import source_key
from app.services.jobs import JobService, validate_sources
from app.services.retrieval import group_by_chars, label_chunks, related_chunks
from app.services.stories import apply_content_change, build_content, create_story_record
from app.services.versioning import create_version, snapshot

logger = logging.getLogger(__name__)


def start_analysis(uow: UnitOfWork, project_id: int, audio_ids: list[int], document_ids: list[int], author: str) -> dict:
    """US-13 CA01/CA02: as novas fontes já estão associadas ao projeto; inicia a análise."""
    validate_sources(uow, project_id, audio_ids, document_ids)
    job = JobService(uow).create(
        project_id, JobType.UPDATE_ANALYSIS,
        {"audio_ids": audio_ids, "documento_ids": document_ids, "responsavel": author},
    )
    return presenters.job(job)


def _analyze_story(container: Container, story: dict, chunks: list[dict]) -> dict | None:
    """Chama o LLM de impacto. Retorna dados da sugestão ou None (sem relação)."""
    blocks, mapping = label_chunks(chunks)
    impact = parsing.normalize_impact(
        container.llm.generate_json(prompts.SYSTEM, prompts.impact_prompt(story, blocks)), set(mapping)
    )
    if impact["relacao"] == "sem_relacao":
        return None
    cited = [mapping[label] for label in impact["fontes"]] or chunks
    current = snapshot(story)
    proposal = build_content({**current, **impact["proposta"]}) if impact["proposta"] else current
    return {
        "historia_id": story["id"], "tipo": impact["relacao"], "justificativa": impact["justificativa"]
        or "O modelo identificou relação entre a história e os novos trechos.",
        "conteudo_atual": current, "conteudo_proposto": proposal,
        "evidencias": [evidence_spec(c) for c in cited], "chunk_ids": {c["id"] for c in cited},
    }


def _new_requirements(container: Container, chunks: list[dict], existing: list[dict]) -> list[dict]:
    """US-14 CA03: trechos sem correspondência viram candidatos a novo requisito."""
    candidates = []
    for batch in group_by_chars(chunks, container.settings.generation_batch_chars):
        candidates.extend(extract_candidates(container, batch))
    suggestions = []
    for c in deduplicate(candidates):
        if any(similarity(f"{s['perfil'] or ''} {s['funcionalidade'] or ''}", story_key(c)) >= EXISTING_DUPLICATE_THRESHOLD
               for s in existing):
            continue
        suggestions.append({
            "historia_id": None, "tipo": SuggestionType.NEW_REQUIREMENT.value,
            "justificativa": "Informação nova sem correspondência clara com as histórias existentes.",
            "conteudo_atual": None, "conteudo_proposto": {
                **build_content(c), "informacoes_ausentes": c["informacoes_ausentes"],
                "afirmacoes_sem_evidencia": c["afirmacoes_sem_evidencia"],
            },
            "evidencias": c["evidencias"], "chunk_ids": set(),
        })
    return suggestions


def run_update_analysis(container: Container, job_id: str) -> None:
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    keys = [source_key(SourceType.AUDIO.value, a) for a in params["audio_ids"]]
    keys += [source_key(SourceType.DOCUMENT.value, d) for d in params["documento_ids"]]
    try:
        with container.uow() as uow:
            validate_sources(uow, project_id, params["audio_ids"], params["documento_ids"])
            stories = uow.stories.list_active(project_id)
            new_chunks = [c for a in params["audio_ids"] for c in uow.chunks.list_by_source("audio", a)]
            new_chunks += [c for d in params["documento_ids"] for c in uow.chunks.list_by_source("documento", d)]
            related = {
                s["id"]: [c for c, _ in related_chunks(uow, container, project_id, s["historia"],
                                                        container.settings.rag_top_k, keys)]
                for s in stories
            }
        drafts, used, failures, attempts = [], set(), [], 0
        for story in stories:
            if not related[story["id"]]:
                continue
            attempts += 1
            try:
                draft = _analyze_story(container, story, related[story["id"]])
            except AppError as exc:
                failures.append({"historia": story["codigo"], "erro": exc.message})
                continue
            if draft:
                drafts.append(draft)
                used |= draft["chunk_ids"]
        unmatched = [c for c in new_chunks if c["id"] not in used]
        if unmatched:
            attempts += 1
            try:
                drafts.extend(_new_requirements(container, unmatched, stories))
            except AppError as exc:
                failures.append({"etapa": "novos_requisitos", "erro": exc.message})
        if attempts and len(failures) == attempts:
            raise AppError("Todas as análises falharam.", {"falhas": failures})
        with container.uow() as uow:
            now = utc_now_iso()
            created = [
                uow.suggestions.insert({
                    "projeto_id": project_id, **{k: v for k, v in d.items() if k != "chunk_ids"},
                    "fontes": keys, "status": SuggestionStatus.PENDING.value, "editada": 0, "job_id": job_id,
                    "criado_em": now, "atualizado_em": now,
                })
                for d in drafts
            ]
            JobService(uow).mark(job_id, JobStatus.COMPLETED, result={
                "sugestoes_criadas": created, "total": len(created), "historias_analisadas": len(stories),
                "trechos_novos": len(new_chunks), "falhas": failures,
                "mensagem": None if created else "Nenhuma atualização ou novo requisito identificado nas novas fontes.",
            })
    except AppError as exc:
        _fail(container, job_id, exc.message)
    except Exception as exc:
        logger.exception("Falha inesperada no job %s", job_id)
        _fail(container, job_id, f"Erro inesperado: {type(exc).__name__}")


def _fail(container: Container, job_id: str, message: str) -> None:
    with container.uow() as uow:
        JobService(uow).mark(job_id, JobStatus.ERROR, error=message)


EDITABLE_FIELDS = ("titulo", "perfil", "funcionalidade", "beneficio", "criterios_aceitacao")


class UpdateService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def _get(self, suggestion_id: int) -> dict:
        suggestion = self.uow.suggestions.get(suggestion_id)
        if not suggestion:
            raise NotFoundError.for_entity("Sugestão de atualização", suggestion_id)
        return suggestion

    def _present(self, suggestion: dict, with_comparison: bool = True) -> dict:
        story = self.uow.stories.get(suggestion["historia_id"]) if suggestion["historia_id"] else None
        return presenters.suggestion(suggestion, story, with_comparison)

    def _require_pending(self, suggestion: dict) -> None:
        if suggestion["status"] != SuggestionStatus.PENDING.value:
            raise ConflictError(f"A sugestão já foi decidida (status '{suggestion['status']}').")

    def list(self, project_id: int, status: str | None = None) -> dict:
        require_project(self.uow, project_id)
        items = self.uow.suggestions.list_by_project(project_id, status)
        return presenters.listing([self._present(s, with_comparison=False) for s in items])

    def get(self, suggestion_id: int) -> dict:
        """US-14 CA02: comparação entre conteúdo atual e proposto."""
        return self._present(self._get(suggestion_id))

    def edit(self, suggestion_id: int, changes: dict[str, Any]) -> dict:
        """US-15 CA01 (editar): altera o conteúdo proposto antes da decisão."""
        suggestion = self._get(suggestion_id)
        self._require_pending(suggestion)
        proposal = suggestion["conteudo_proposto"]
        merged = {**proposal, **build_content({**proposal, **changes})}
        self.uow.suggestions.update(suggestion_id, {
            "conteudo_proposto": merged, "editada": 1, "atualizado_em": utc_now_iso()
        })
        return self.get(suggestion_id)

    def approve(self, suggestion_id: int, author: str, changes: dict[str, Any] | None = None) -> dict:
        """US-15 CA02/CA04, US-18 CA01: aplica a sugestão, cria versão e registra novas evidências."""
        if changes:
            self.edit(suggestion_id, changes)
        suggestion = self._get(suggestion_id)
        self._require_pending(suggestion)
        origin = ChangeOrigin.AI_SUGGESTION_EDITED if suggestion["editada"] else ChangeOrigin.AI_SUGGESTION
        proposal = suggestion["conteudo_proposto"]
        values: dict[str, Any] = {"status": SuggestionStatus.APPROVED.value}
        if suggestion["historia_id"]:
            story = self.uow.stories.get(suggestion["historia_id"])
            if not story:
                raise ConflictError("A história desta sugestão não existe mais.")
            apply_content_change(self.uow, story, {k: proposal.get(k) for k in EDITABLE_FIELDS})
            add_evidences(self.uow, story["id"], suggestion["evidencias"], "atualizacao")
            values["versao_aplicada"] = create_version(
                self.uow, story["id"], ChangeType.AUTOMATIC, origin, author, suggestion_id,
                note=suggestion["justificativa"],
            )
        else:
            values["historia_criada_id"] = create_story_record(
                self.uow, suggestion["projeto_id"], proposal, origin="ia", change_type=ChangeType.AUTOMATIC,
                change_origin=origin, author=author, evidence_specs=suggestion["evidencias"],
                evidence_origin="atualizacao", suggestion_id=suggestion_id,
            )
            values["versao_aplicada"] = 1
        now = utc_now_iso()
        self.uow.suggestions.update(suggestion_id, {**values, "decidido_por": author, "decidido_em": now, "atualizado_em": now})
        return self.get(suggestion_id)

    def reject(self, suggestion_id: int, author: str) -> dict:
        """US-15 CA03: a história é preservada."""
        suggestion = self._get(suggestion_id)
        self._require_pending(suggestion)
        now = utc_now_iso()
        self.uow.suggestions.update(suggestion_id, {
            "status": SuggestionStatus.REJECTED.value, "decidido_por": author, "decidido_em": now, "atualizado_em": now,
        })
        return self.get(suggestion_id)
