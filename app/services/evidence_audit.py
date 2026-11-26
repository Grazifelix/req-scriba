"""Auditoria semântica das evidências (prompt VALIDACAO_EVIDENCIAS).

Verifica se cada evidência sustenta de fato a afirmação a que está ligada: os componentes da história
(perfil, funcionalidade, benefício), cada um com os trechos que declaram sustentá-lo, e cada critério de
aceitação, com os trechos dele. Cada item é avaliado só pelos seus próprios trechos, para que uma evidência
compatível com a história não justifique um critério mais específico.

Regras do código (não dependem do LLM):
- item sem trecho vinculado é SEM_EVIDENCIA e nem vai ao modelo como algo a julgar;
- item que o modelo não devolveu fica INSUFICIENTE, com o problema registrado;
- o resumo numérico é contado aqui, nunca copiado do modelo.

A auditoria nunca altera a história nem os critérios: o resultado é gravado em ``auditorias_evidencia`` para
revisão humana (``review``), e ``correcao_proposta`` é só uma sugestão de texto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.container import Container
from app.core.errors import AppError, BusinessValidationError, ExternalServiceError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import JobStatus, JobType, StoryStatus
from app.llm import parsing, prompts
from app.services import presenters
from app.services.common import require_project, require_story
from app.services.criteria import evidence_chunks
from app.services.jobs import JobService
from app.services.retrieval import describe_chunk

logger = logging.getLogger(__name__)

COMPONENTS = ("perfil", "funcionalidade", "beneficio")
NO_EVIDENCE = "SEM_EVIDENCIA"
NOT_RETURNED = "O modelo não avaliou este item."
REVIEW_DECISIONS = ("confirma", "contesta")
ORIGINS = ("geracao", "sob_demanda", "lote")


@dataclass
class AuditTarget:
    ref: str
    story: dict[str, Any]
    evidences: list[dict[str, Any]]
    criteria: list[dict[str, Any]]
    # preenchidos ao montar o lote
    component_labels: dict[str, list[str]] = field(default_factory=dict)
    criterion_labels: dict[str, list[str]] = field(default_factory=dict)


def _component_evidences(story: dict, evidences: list[dict]) -> dict[str, list[dict]]:
    """Evidências de cada componente: as que declaram sustentá-lo; sem nenhuma declaração (evidências antigas),
    todas as evidências da história valem para todos os componentes."""
    declared = any(e.get("afirmacoes_sustentadas") for e in evidences)
    result = {}
    for name in COMPONENTS:
        if story.get(name):
            result[name] = [e for e in evidences if not declared or name in (e.get("afirmacoes_sustentadas") or [])]
    return result


def _size(target: AuditTarget) -> int:
    return sum(len(e["trecho"]) for e in target.evidences) + len(target.story.get("historia") or "")


def _batches(targets: list[AuditTarget], budget: int) -> list[list[AuditTarget]]:
    groups: list[list[AuditTarget]] = []
    size = 0
    for target in targets:
        if not groups or (groups[-1] and size + _size(target) > budget):
            groups.append([])
            size = 0
        groups[-1].append(target)
        size += _size(target)
    return groups


def _label_batch(batch: list[AuditTarget]) -> tuple[list[dict], dict[str, dict], list[str]]:
    """Rótulos F por trecho no lote; preenche os rótulos de cada componente e critério dos alvos."""
    blocks, mapping, by_chunk = [], {}, {}

    def label_of(evidence: dict) -> str | None:
        chunk = (evidence_chunks([evidence]) or [None])[0]
        if chunk is None:
            return None
        if chunk["id"] not in by_chunk:
            label = f"F{len(mapping) + 1}"
            by_chunk[chunk["id"]] = label
            mapping[label] = {**chunk, "evidencia_id": evidence["id"]}
            blocks.append({"rotulo": label, "descricao": describe_chunk(chunk), "texto": chunk["texto"]})
        return by_chunk[chunk["id"]]

    stories = []
    for target in batch:
        by_evidence = {e["id"]: e for e in target.evidences}
        by_chunk_id = {e["trecho_id"]: e for e in target.evidences if e.get("trecho_id") is not None}
        target.component_labels = {
            name: [l for l in (label_of(e) for e in items) if l]
            for name, items in _component_evidences(target.story, target.evidences).items()
        }
        target.criterion_labels = {}
        criteria = []
        for index, c in enumerate(target.criteria, start=1):
            ref = f"{target.ref}-CA{index}"
            linked = [by_evidence.get(f.get("evidencia_id")) or by_chunk_id.get(f.get("trecho_id"))
                      for f in c.get("fontes") or []]
            labels = [l for l in (label_of(e) for e in linked if e) if l]
            target.criterion_labels[ref] = labels
            criteria.append((ref, c["criterio"], labels))
        stories.append(prompts.audit_story_block(target.ref, target.story, target.component_labels, criteria))
    return blocks, mapping, stories


def _evidence_entries(entries: list[dict], mapping: dict[str, dict]) -> list[dict]:
    return [{"evidencia_id": mapping[e["rotulo"]]["evidencia_id"], "trecho_id": mapping[e["rotulo"]]["id"],
             "resultado": e["resultado"], "justificativa": e["justificativa"]} for e in entries]


def _item(labels: list[str], audited: dict | None, mapping: dict[str, dict]) -> dict[str, Any]:
    if not labels:
        return {"resultado": NO_EVIDENCE, "origem_resultado": "codigo", "evidencias_avaliadas": [],
                "problemas": ["Nenhum trecho das fontes está vinculado a esta afirmação."], "correcao_proposta": None,
                "componentes_sem_sustentacao": []}
    if audited is None:
        return {"resultado": parsing.UNDECIDED, "origem_resultado": "codigo", "evidencias_avaliadas": [],
                "problemas": [NOT_RETURNED], "correcao_proposta": None, "componentes_sem_sustentacao": []}
    return {"resultado": audited["resultado"], "origem_resultado": "llm",
            "evidencias_avaliadas": _evidence_entries(audited["evidencias_avaliadas"], mapping),
            "problemas": audited["problemas"], "correcao_proposta": audited["correcao_proposta"],
            "componentes_sem_sustentacao": audited["componentes_sem_sustentacao"]}


def summarize(components: list[dict], criteria: list[dict]) -> dict[str, int]:
    """Contagem por classificação (componentes + critérios), feita pelo código."""
    keys = {"COMPATIVEL": "compativeis", "PARCIALMENTE_COMPATIVEL": "parcialmente_compativeis",
            "INCOMPATIVEL": "incompativeis", "INSUFICIENTE": "insuficientes", "SEM_EVIDENCIA": "sem_evidencia"}
    summary = {"total_afirmacoes_avaliadas": len(components) + len(criteria), **{v: 0 for v in keys.values()}}
    for item in components + criteria:
        summary[keys[item["resultado"]]] += 1
    return summary


def _record(target: AuditTarget, audited: dict | None, mapping: dict[str, dict], general: list[str]) -> dict:
    audited = audited or {"componentes": {}, "criterios": {}}
    components = [
        {"componente": name, "afirmacao": target.story.get(name),
         **_item(labels, audited["componentes"].get(name), mapping)}
        for name, labels in target.component_labels.items()
    ]
    criteria = [
        {"criterio_id": c["id"], "codigo": c["codigo"], "criterio": c["criterio"],
         **_item(target.criterion_labels[f"{target.ref}-CA{i}"], audited["criterios"].get(f"{target.ref}-CA{i}"), mapping)}
        for i, c in enumerate(target.criteria, start=1)
    ]
    return {"componentes": components, "criterios": criteria, "problemas_conjunto": general,
            "resumo": summarize(components, criteria)}


def audit_targets(container: Container, targets: list[AuditTarget]) -> tuple[dict[str, dict], list[dict]]:
    """Uma chamada ao LLM por lote de histórias. Retorna (ref → registro da auditoria, falhas)."""
    records: dict[str, dict] = {}
    failures: list[dict] = []
    for batch in _batches(targets, container.settings.generation_batch_chars):
        blocks, mapping, stories = _label_batch(batch)
        if not blocks:  # nada a julgar: tudo é SEM_EVIDENCIA, decidido pelo código
            records.update({t.ref: _record(t, None, mapping, []) for t in batch})
            continue
        expected = {t.ref: {"componentes": {k: set(v) for k, v in t.component_labels.items()},
                            "criterios": {k: set(v) for k, v in t.criterion_labels.items()}} for t in batch}
        try:
            data = container.llm.generate_json(prompts.SYSTEM, prompts.audit_prompt(stories, blocks),
                                               schema=prompts.AUDIT_SCHEMA)
        except AppError as exc:
            logger.warning("Lote da auditoria falhou (%s): %s", ", ".join(t.ref for t in batch), exc.message)
            failures += [{"historia": t.ref, "erro": exc.message} for t in batch]
            continue
        result = parsing.normalize_audit_result(data, expected)
        for t in batch:
            records[t.ref] = _record(t, result["historias"].get(t.ref), mapping, result["problemas_conjunto"])
    return records, failures


def target_for(uow: UnitOfWork, story: dict) -> AuditTarget:
    return AuditTarget(f"H{story['id']}", story, uow.evidences.list_by_story(story["id"]),
                       uow.criteria.list_current(story["id"]))


def save_audit(uow: UnitOfWork, container: Container, story: dict, record: dict, origin: str,
               job_id: str | None = None) -> int:
    return uow.evidence_audits.insert({
        "historia_id": story["id"], "versao_historia": story["versao_atual"], "origem": origin, "job_id": job_id,
        **record, "revisoes": [], "modelo": getattr(container.llm, "model", None),
        "versao_prompt": prompts.template_version(prompts.EVIDENCE_AUDIT), "criado_em": utc_now_iso(),
    })


def audit_and_save(container: Container, story_ids: list[int], origin: str, job_id: str | None = None
                   ) -> tuple[list[int], list[dict], dict[str, int]]:
    """Audita e grava cada lote. Retorna (ids das auditorias, falhas, resumo somado)."""
    with container.uow() as uow:
        stories = {s["id"]: s for s in (uow.stories.get(i) for i in story_ids) if s}
        targets = [target_for(uow, s) for s in stories.values()]
    records, failures = audit_targets(container, targets)
    saved, total = [], summarize([], [])
    with container.uow() as uow:
        for target in targets:
            if target.ref in records:
                saved.append(save_audit(uow, container, target.story, records[target.ref], origin, job_id))
                for key, value in records[target.ref]["resumo"].items():
                    total[key] += value
    return saved, [{**f, "historia_id": int(f["historia"][1:])} for f in failures], total


class EvidenceAuditService:
    def __init__(self, uow: UnitOfWork, container: Container | None = None) -> None:
        self.uow = uow
        self.container = container

    def audit(self, story_id: int) -> dict:
        """Auditoria sob demanda de uma história (síncrona). Falha do LLM → 502 e nada é gravado."""
        story = require_story(self.uow, story_id)
        target = target_for(self.uow, story)
        records, failures = audit_targets(self.container, [target])
        if failures:
            raise ExternalServiceError(f"Falha na auditoria das evidências: {failures[0]['erro']}", {"falhas": failures})
        audit_id = save_audit(self.uow, self.container, story, records[target.ref], "sob_demanda")
        return self.get(audit_id)

    def _get(self, audit_id: int) -> dict:
        audit = self.uow.evidence_audits.get(audit_id)
        if not audit:
            raise NotFoundError.for_entity("Auditoria de evidências", audit_id)
        return audit

    def get(self, audit_id: int) -> dict:
        audit = self._get(audit_id)
        return presenters.evidence_audit(audit, self.uow.stories.get(audit["historia_id"]))

    def list(self, story_id: int) -> dict:
        story = require_story(self.uow, story_id)
        return {"historia_id": story_id, **presenters.listing(
            [presenters.evidence_audit(a, story) for a in self.uow.evidence_audits.list_by_story(story_id)])}

    def review(self, audit_id: int, item: str, decision: str, note: str | None, author: str) -> dict:
        """Revisão humana de um item ("componente:perfil" ou "criterio:<id>"): confirma ou contesta o resultado
        do modelo. Fica registrada na auditoria; a história não muda."""
        audit = self._get(audit_id)
        kind, _, key = item.partition(":")
        known = ({f"componente:{c['componente']}" for c in audit["componentes"]}
                 | {f"criterio:{c['criterio_id']}" for c in audit["criterios"]})
        if decision not in REVIEW_DECISIONS or f"{kind}:{key}" not in known:
            raise BusinessValidationError("Item ou decisão inválidos.", {"itens_validos": sorted(known),
                                                                        "decisoes": list(REVIEW_DECISIONS)})
        revisions = [r for r in audit["revisoes"] if r["item"] != item]
        revisions.append({"item": item, "decisao": decision, "observacao": note, "responsavel": author,
                          "data_hora": utc_now_iso()})
        self.uow.evidence_audits.update(audit_id, {"revisoes": revisions})
        return self.get(audit_id)


# -- Lote (job) ---------------------------------------------------------------
def start_evidence_audit(uow: UnitOfWork, project_id: int, story_ids: list[int], author: str) -> dict:
    require_project(uow, project_id)
    job = JobService(uow).create(project_id, JobType.EVIDENCE_AUDIT, {"historia_ids": story_ids, "responsavel": author})
    return presenters.job(job)


def run_evidence_audit(container: Container, job_id: str) -> None:
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
        ids = job["parametros"]["historia_ids"] or [
            s["id"] for s in uow.stories.list_active(job["projeto_id"]) if s["status"] != StoryStatus.REJECTED.value]
        ids = [i for i in ids if (s := uow.stories.get(i)) and s["projeto_id"] == job["projeto_id"]]
    try:
        saved, failures, total = audit_and_save(container, ids, "lote", job_id)
        if ids and not saved:
            raise AppError("Todas as auditorias falharam.", {"falhas": failures})
        with container.uow() as uow:
            JobService(uow).mark(job_id, JobStatus.COMPLETED, result={
                "auditorias_criadas": saved, "total": len(saved), "falhas": failures, "resumo": total,
                "versoes_prompt": prompts.prompt_versions(),
                "mensagem": None if ids else "Nenhuma história para auditar.",
            })
    except AppError as exc:
        with container.uow() as uow:
            JobService(uow).mark(job_id, JobStatus.ERROR, result={"detalhes": exc.details} if exc.details else None,
                                 error=exc.message)
    except Exception as exc:  # noqa: BLE001 - registrado e convertido em status de erro
        logger.exception("Falha inesperada no job %s", job_id)
        with container.uow() as uow:
            JobService(uow).mark(job_id, JobStatus.ERROR, error=f"Erro inesperado: {type(exc).__name__}")
