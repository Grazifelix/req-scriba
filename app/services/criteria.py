"""Critérios de aceitação gerados à parte das histórias (prompt GERACAO_CRITERIOS_ACEITACAO).

A etapa recebe histórias já formuladas e as evidências delas e nunca altera a história: só escreve os
critérios. Três usos compartilham o mesmo núcleo (``generate_criteria``):

- no pipeline de geração, sobre as candidatas, antes de gravá-las (``apply_to_candidate``);
- sob demanda, para uma história gravada (``CriteriaService.generate``);
- em lote, num job (``run_criteria_generation``).

Os critérios citam rótulos F de trechos; o código resolve os rótulos para trechos reais, confere as citações
e sinaliza o que não tem sustentação. Um trecho citado por um critério que ainda não era evidência da história
passa a ser (o texto vem do banco, nunca do LLM). Histórias APROVADAS não recebem critérios automaticamente:
o LLM nunca escreve numa história aprovada (``409``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.container import Container
from app.core.errors import AppError, ConflictError, ExternalServiceError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import ChangeOrigin, ChangeType, JobStatus, JobType, StoryStatus
from app.domain.text import similarity
from app.domain.traceability import (
    CRITERIA_ELEMENT, CRITERION_WITHOUT_EVIDENCE, INVALID_REFERENCE, without_criteria_gaps,
)
from app.llm import parsing, prompts
from app.services import presenters
from app.services.analysis import analysis_text
from app.services.candidates import DUPLICATE_THRESHOLD, citation_problems
from app.services.common import require_project, require_story
from app.services.evidence import add_evidences, evidence_spec
from app.services.jobs import JobService
from app.services.retrieval import describe_chunk, related_chunks
from app.services.stories import apply_content_change
from app.services.versioning import create_version

logger = logging.getLogger(__name__)

NO_BASIS = "As fontes não trazem base suficiente para critérios de aceitação desta história."
FAILED = "A geração de critérios de aceitação falhou para esta história; gere os critérios novamente."
NOT_RETURNED = "O modelo não devolveu critérios para esta história."
CRITERION_CLAIM = "Sustenta critério de aceitação"
RELATED_TAG = "relacionado"


@dataclass
class CriteriaTarget:
    """Uma história a receber critérios: conteúdo, evidências (como trechos) e trechos relacionados (RAG)."""

    ref: str
    story: dict[str, Any]
    evidences: list[dict[str, Any]]
    related: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CriteriaOutcome:
    criteria: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    new_evidences: list[dict[str, Any]]


def evidence_chunks(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evidências (gravadas ou especificações) no formato de trecho, para rotular no prompt."""
    chunks, seen = [], set()
    for e in evidences:
        chunk_id = e.get("trecho_id")
        if chunk_id is None or chunk_id in seen:
            continue
        seen.add(chunk_id)
        chunks.append({
            "id": chunk_id, "tipo_fonte": e["tipo_fonte"], "fonte_id": e["fonte_id"],
            "nome_arquivo": e["nome_arquivo"], "texto": e["trecho"], "localizacao": e["localizacao"],
        })
    return chunks


def _target_size(target: CriteriaTarget) -> int:
    return len(target.story.get("historia") or "") + sum(len(c["texto"]) for c in target.evidences + target.related)


def _batches(targets: list[CriteriaTarget], budget: int) -> list[list[CriteriaTarget]]:
    """Várias histórias por chamada, até o orçamento de caracteres (uma história grande vai sozinha)."""
    groups: list[list[CriteriaTarget]] = []
    size = 0
    for target in targets:
        length = _target_size(target)
        if not groups or (groups[-1] and size + length > budget):
            groups.append([])
            size = 0
        groups[-1].append(target)
        size += length
    return groups


def _label_batch(batch: list[CriteriaTarget]) -> tuple[list[dict], dict[str, dict], dict[str, list[str]]]:
    """Rótulos F únicos no lote: (blocos do prompt, rótulo → trecho, ref da história → rótulos das evidências)."""
    blocks, mapping, by_chunk, story_labels = [], {}, {}, {}
    for target in batch:
        labels = []
        for chunk, related in [(c, False) for c in target.evidences] + [(c, True) for c in target.related]:
            label = by_chunk.get(chunk["id"])
            if label is None:
                label = f"F{len(mapping) + 1}"
                by_chunk[chunk["id"]] = label
                mapping[label] = chunk
                tag = f" — {RELATED_TAG}" if related else ""
                blocks.append({"rotulo": label, "descricao": describe_chunk(chunk) + tag, "texto": chunk["texto"]})
            if not related:
                labels.append(label)
        story_labels[target.ref] = labels
    return blocks, mapping, story_labels


def _criterion(raw: dict, mapping: dict[str, dict]) -> dict[str, Any]:
    """Critério normalizado → formato gravado: rótulos viram ids de trecho; problemas viram sinalizações."""
    sources = {label: [chunk] for label, chunk in mapping.items()}
    claims = list(raw["afirmacoes_sem_evidencia"])
    claims += [p for p in citation_problems(raw["fontes_detalhe"], sources) if p not in claims]
    claims += [INVALID_REFERENCE.format(label) for label in raw["fontes_invalidas"]]
    if not raw["fontes_detalhe"]:
        claims.append(CRITERION_WITHOUT_EVIDENCE)
    return {
        "titulo": raw["titulo"], "criterio": raw["criterio"],
        "dado": raw["dado"], "quando": raw["quando"], "entao": raw["entao"],
        "fontes": [{"trecho_id": mapping[e["rotulo"]]["id"], "afirmacoes_sustentadas": e["afirmacoes"]}
                   for e in raw["fontes_detalhe"]],
        "informacoes_ausentes": raw["informacoes_ausentes"], "afirmacoes_sem_evidencia": claims, "origem": "ia",
    }


def _outcome(target: CriteriaTarget, result: dict | None, mapping: dict[str, dict]) -> CriteriaOutcome:
    if result is None:
        return CriteriaOutcome([], [{"elemento": CRITERIA_ELEMENT, "descricao": NOT_RETURNED}], [])
    criteria: list[dict[str, Any]] = []
    for raw in result["criterios"]:
        item = _criterion(raw, mapping)
        if all(similarity(item["criterio"], c["criterio"]) < DUPLICATE_THRESHOLD for c in criteria):
            criteria.append(item)
    gaps = [{"elemento": CRITERIA_ELEMENT, "descricao": g["descricao"]} for g in result["lacunas"] if g["descricao"]]
    if not criteria and not gaps:
        gaps.append({"elemento": CRITERIA_ELEMENT, "descricao": NO_BASIS})
    known = {c["id"] for c in target.evidences}
    cited = {f["trecho_id"] for c in criteria for f in c["fontes"]} - known
    chunks = {c["id"]: c for c in mapping.values()}
    return CriteriaOutcome(criteria, gaps, [evidence_spec(chunks[i], CRITERION_CLAIM) for i in sorted(cited)])


BatchCallback = Callable[[dict[str, CriteriaOutcome]], None]


def generate_criteria(
    container: Container,
    targets: list[CriteriaTarget],
    analysis: str | None = None,
    on_batch: BatchCallback | None = None,
) -> tuple[dict[str, CriteriaOutcome], list[dict[str, Any]]]:
    """Chama o LLM por lote de histórias. Retorna (ref → resultado, falhas). Um lote que falha não derruba
    os demais; ``on_batch`` recebe os resultados de cada lote assim que ele termina (persistência gradual)."""
    outcomes: dict[str, CriteriaOutcome] = {}
    failures: list[dict[str, Any]] = []
    for batch in _batches(targets, container.settings.generation_batch_chars):
        blocks, mapping, story_labels = _label_batch(batch)
        stories = [prompts.criteria_story_block(t.ref, t.story, story_labels[t.ref]) for t in batch]
        try:
            data = container.llm.generate_json(
                prompts.SYSTEM, prompts.criteria_prompt(stories, blocks, analysis), schema=prompts.CRITERIA_SCHEMA,
            )
        except AppError as exc:
            logger.warning("Lote de critérios falhou (%s): %s", ", ".join(t.ref for t in batch), exc.message)
            failures += [{"historia": t.ref, "erro": exc.message} for t in batch]
            continue
        result = parsing.normalize_criteria_result(data, {t.ref for t in batch}, set(mapping))
        batch_outcomes = {t.ref: _outcome(t, result["historias"].get(t.ref), mapping) for t in batch}
        outcomes.update(batch_outcomes)
        if on_batch:
            on_batch(batch_outcomes)
    return outcomes, failures


def related_for(
    uow: UnitOfWork, container: Container, project_id: int, story: dict, known_ids: set[int],
    source_keys: list[str] | None = None,
) -> list[dict]:
    """Trechos relacionados à história (RAG) que ainda não são evidência dela, para regras e comportamentos."""
    limit = container.settings.criteria_related_chunks
    if limit <= 0:
        return []
    hits = related_chunks(uow, container, project_id, story.get("historia") or "", limit + len(known_ids), source_keys)
    return [chunk for chunk, _ in hits if chunk["id"] not in known_ids][:limit]


def apply_to_candidate(candidate: dict, outcome: CriteriaOutcome | None) -> None:
    """Pipeline: critérios e lacunas entram na candidata antes de ela ser gravada (a v1 já os contém)."""
    if outcome is None:
        outcome = CriteriaOutcome([], [{"elemento": CRITERIA_ELEMENT, "descricao": FAILED}], [])
    candidate["criterios_aceitacao"] = outcome.criteria
    candidate["evidencias"] = candidate["evidencias"] + outcome.new_evidences
    gaps = without_criteria_gaps(candidate.get("informacoes_ausentes") or [])
    candidate["informacoes_ausentes"] = gaps + outcome.gaps


def _apply_to_story(uow: UnitOfWork, story_id: int, outcome: CriteriaOutcome, author: str) -> int | None:
    """História gravada: novas evidências → critérios → lacunas → nova versão (origem geracao_criterios_ia).
    Retorna o número da versão criada, ou None se nada mudou."""
    story = uow.stories.get(story_id)
    added = add_evidences(uow, story_id, outcome.new_evidences, "extracao")
    changed = apply_content_change(uow, story, {"criterios_aceitacao": outcome.criteria})
    current = uow.stories.get(story_id)
    gaps = without_criteria_gaps(current["informacoes_ausentes"]) + outcome.gaps
    if gaps != current["informacoes_ausentes"]:
        uow.stories.update(story_id, {"informacoes_ausentes": gaps, "atualizado_em": utc_now_iso()})
    if not (changed or added):
        return None
    return create_version(uow, story_id, ChangeType.AUTOMATIC, ChangeOrigin.AI_CRITERIA, author,
                          note="Critérios de aceitação gerados pela IA a partir das evidências da história.")


def _require_not_approved(story: dict) -> None:
    if story["status"] == StoryStatus.APPROVED.value:
        raise ConflictError(
            "A história já foi aprovada: o LLM não altera histórias aprovadas. Edite os critérios manualmente.",
            {"motivo": "historia_aprovada", "historia_id": story["id"]},
        )


def _target(uow: UnitOfWork, container: Container, story: dict) -> CriteriaTarget:
    evidences = evidence_chunks(uow.evidences.list_by_story(story["id"]))
    related = related_for(uow, container, story["projeto_id"], story, {c["id"] for c in evidences})
    return CriteriaTarget(f"H{story['id']}", story, evidences, related)


def _project_analysis(uow: UnitOfWork, project_id: int) -> str | None:
    return analysis_text(uow, uow.analyses.latest_reviewed(project_id), for_stories=True)


class CriteriaService:
    """Geração de critérios sob demanda para uma história gravada (síncrona, uma chamada ao LLM)."""

    def __init__(self, uow: UnitOfWork, container: Container) -> None:
        self.uow = uow
        self.container = container

    def generate(self, story_id: int, author: str) -> dict:
        story = require_story(self.uow, story_id)
        _require_not_approved(story)
        target = _target(self.uow, self.container, story)
        outcomes, failures = generate_criteria(
            self.container, [target], _project_analysis(self.uow, story["projeto_id"])
        )
        if failures:
            raise ExternalServiceError(f"Falha ao gerar critérios: {failures[0]['erro']}", {"falhas": failures})
        _apply_to_story(self.uow, story_id, outcomes[target.ref], author)
        from app.services.stories import StoryService  # import local: evita ciclo services.stories ↔ criteria

        return StoryService(self.uow).detail(story_id)


# -- Lote (job) ---------------------------------------------------------------
def select_stories(uow: UnitOfWork, project_id: int, story_ids: list[int], replace: bool) -> tuple[list[dict], list[dict]]:
    """(histórias a processar, ignoradas com motivo). Sem ids: todas as ativas não aprovadas do projeto."""
    if story_ids:
        stories = [s for s in (uow.stories.get(i) for i in story_ids) if s and s["projeto_id"] == project_id]
        missing = set(story_ids) - {s["id"] for s in stories}
        skipped = [{"historia_id": i, "motivo": "não encontrada neste projeto"} for i in sorted(missing)]
    else:
        stories, skipped = uow.stories.list_active(project_id), []
    selected = []
    for s in stories:
        if s["status"] == StoryStatus.APPROVED.value:
            skipped.append({"historia_id": s["id"], "codigo": s["codigo"], "motivo": "historia_aprovada"})
        elif s["status"] == StoryStatus.REJECTED.value:
            skipped.append({"historia_id": s["id"], "codigo": s["codigo"], "motivo": "historia_desaprovada"})
        elif s["criterios_aceitacao"] and not replace:
            skipped.append({"historia_id": s["id"], "codigo": s["codigo"], "motivo": "ja_possui_criterios"})
        else:
            selected.append(s)
    return selected, skipped


def start_criteria_generation(
    uow: UnitOfWork, project_id: int, story_ids: list[int], replace: bool, author: str
) -> dict:
    require_project(uow, project_id)
    job = JobService(uow).create(
        project_id, JobType.CRITERIA_GENERATION,
        {"historia_ids": story_ids, "substituir": replace, "responsavel": author},
    )
    return presenters.job(job)


def run_criteria_generation(container: Container, job_id: str) -> None:
    """Job de critérios em lote: cada lote de histórias é gravado assim que o LLM responde."""
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    author = params.get("responsavel") or "sistema"
    try:
        with container.uow() as uow:
            stories, skipped = select_stories(uow, project_id, params["historia_ids"], params["substituir"])
            targets = [_target(uow, container, s) for s in stories]
            analysis = _project_analysis(uow, project_id)
        ids = {f"H{s['id']}": s["id"] for s in stories}
        updated: list[int] = []

        def persist(batch: dict[str, CriteriaOutcome]) -> None:
            with container.uow() as uow:
                for ref, outcome in batch.items():
                    if _apply_to_story(uow, ids[ref], outcome, author) is not None:
                        updated.append(ids[ref])

        _, failures = generate_criteria(container, targets, analysis, on_batch=persist)
        if targets and len(failures) == len(targets):
            raise AppError("Todas as gerações de critérios falharam.", {"falhas": failures})
        failures = [{**f, "historia_id": ids[f["historia"]]} for f in failures]
        with container.uow() as uow:
            JobService(uow).mark(job_id, JobStatus.COMPLETED, result={
                "historias_atualizadas": updated, "total": len(updated), "ignoradas": skipped, "falhas": failures,
                "versoes_prompt": prompts.prompt_versions(),
                "mensagem": None if targets else "Nenhuma história elegível para gerar critérios.",
            })
    except AppError as exc:
        with container.uow() as uow:
            JobService(uow).mark(job_id, JobStatus.ERROR, result={"detalhes": exc.details} if exc.details else None,
                                 error=exc.message)
    except Exception as exc:  # noqa: BLE001 - registrado e convertido em status de erro
        logger.exception("Falha inesperada no job %s", job_id)
        with container.uow() as uow:
            JobService(uow).mark(job_id, JobStatus.ERROR, error=f"Erro inesperado: {type(exc).__name__}")
