"""Análise das fontes: como a IA entendeu a reunião (sistema, usuários, requisitos de negócio,
funcionalidades e itens fora de escopo).

A análise é gerada por um job próprio (``app.services.source_analysis``), antes e independentemente da geração
de histórias; com novas fontes, ela atualiza a análise mais recente e grava uma nova. O analista revisa, edita
e aprova (``revisada``); só a análise aprovada orienta a geração de histórias. As evidências dos itens são
cópias dos trechos reais do banco (nunca texto do LLM).
"""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import BusinessValidationError, ConflictError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import AnalysisItemType, JobType, SourceType
from app.domain.text import similarity
from app.llm.parsing import ANALYSIS_CODE_PREFIX
from app.services import presenters
from app.services.common import require_project
from app.services.evidence import evidence_spec, specs_for_labels

SIMILAR_ITEM_THRESHOLD = 0.85
TYPE_ORDER = [t.value for t in AnalysisItemType]
FUNCTIONALITY = AnalysisItemType.FUNCTIONALITY.value
BUSINESS_REQUIREMENT = AnalysisItemType.BUSINESS_REQUIREMENT.value


def _next_code(items: list[dict], kind: str) -> str | None:
    prefix = ANALYSIS_CODE_PREFIX.get(kind)
    if not prefix:
        return None
    numbers = [int(m.group(1)) for i in items if (m := re.fullmatch(rf"{prefix}(\d+)", i.get("codigo") or ""))]
    return f"{prefix}{max(numbers, default=0) + 1}"


ITEM_FIELDS = ("tipo", "codigo", "texto", "complemento", "referencias", "evidencias", "historia_ids", "origem", "editado")


class AnalysisBuilder:
    """Junta as análises de um ou mais lotes: mescla itens parecidos e renumera RN/FN globalmente.

    Semeado com a análise anterior (``seed``), mantém os itens, códigos e edições dela e acrescenta os novos.
    """

    def __init__(self) -> None:
        self.sistema: str | None = None
        self.items: list[dict] = []
        self.seeded: list[dict] = []

    def seed(self, analysis: dict, items: list[dict]) -> None:
        self.sistema = analysis.get("sistema")
        self.seeded = [{k: item[k] for k in ITEM_FIELDS} for item in items]
        self.items = list(self.seeded)

    def _match(self, item: dict) -> dict | None:
        if item["codigo"]:  # o prompt de atualização pede para manter os códigos da análise anterior
            same_code = next((i for i in self.seeded if i["tipo"] == item["tipo"] and i["codigo"] == item["codigo"]), None)
            if same_code and similarity(same_code["texto"], item["texto"]) >= SIMILAR_ITEM_THRESHOLD / 2:
                return same_code
        return next(
            (i for i in self.items
             if i["tipo"] == item["tipo"] and similarity(i["texto"], item["texto"]) >= SIMILAR_ITEM_THRESHOLD),
            None,
        )

    def add(self, raw: dict[str, Any], sources: dict[str, list[dict]]) -> dict[str, str]:
        """Incorpora a análise de um lote. Retorna o mapa código local → código global (RN/FN)."""
        self.sistema = self.sistema or raw.get("sistema")
        code_map: dict[str, str] = {}
        pending_refs: list[tuple[dict, list[str]]] = []
        for item in raw.get("itens", []):
            target = self._match(item)
            if target is None:
                target = {
                    "tipo": item["tipo"], "codigo": _next_code(self.items, item["tipo"]), "texto": item["texto"],
                    "complemento": item["complemento"], "referencias": [], "evidencias": [], "historia_ids": [],
                    "origem": "ia", "editado": 0,
                }
                self.items.append(target)
            else:
                target["complemento"] = target["complemento"] or item["complemento"]
            known = {e["trecho_id"] for e in target["evidencias"]}
            target["evidencias"] += [e for e in specs_for_labels(item["fontes"], sources) if e["trecho_id"] not in known]
            if item["codigo"]:
                code_map[item["codigo"]] = target["codigo"]
            if item["tipo"] == FUNCTIONALITY:
                pending_refs.append((target, item["referencias"]))
        for target, refs in pending_refs:  # RN podem vir depois das FN na resposta
            for ref in refs:
                code = code_map.get(ref)
                if code and code not in target["referencias"]:
                    target["referencias"].append(code)
        return code_map

    def is_empty(self) -> bool:
        return not self.sistema and not self.items


def _source_refs(uow: UnitOfWork, audio_ids: list[int], document_ids: list[int]) -> list[dict]:
    refs = [(SourceType.AUDIO.value, a, uow.audios.get(a)) for a in audio_ids]
    refs += [(SourceType.DOCUMENT.value, d, uow.documents.get(d)) for d in document_ids]
    return [{"tipo_fonte": kind, "fonte_id": sid, "nome_arquivo": row["nome_arquivo"]} for kind, sid, row in refs if row]


def save_generated_analysis(
    uow: UnitOfWork, project_id: int, job_id: str, builder: AnalysisBuilder, audio_ids: list[int],
    document_ids: list[int], previous_sources: list[dict] | None = None,
) -> int | None:
    """Grava uma nova análise (não revisada). Retorna o id, ou None quando o modelo não devolveu análise.

    ``previous_sources``: fontes da análise anterior que esta atualiza; ficam registradas junto das novas.
    """
    if builder.is_empty():
        return None
    now = utc_now_iso()
    sources = list(previous_sources or [])
    known = {(s["tipo_fonte"], s["fonte_id"]) for s in sources}
    sources += [s for s in _source_refs(uow, audio_ids, document_ids) if (s["tipo_fonte"], s["fonte_id"]) not in known]
    analysis_id = uow.analyses.insert({
        "projeto_id": project_id, "job_id": job_id, "origem": "ia", "sistema": builder.sistema,
        "fontes": sources, "revisada": 0, "criado_em": now, "atualizado_em": now,
    })
    ordered = sorted(builder.items, key=lambda i: TYPE_ORDER.index(i["tipo"]))
    for position, item in enumerate(ordered):
        uow.analysis_items.insert({
            **{k: item[k] for k in ITEM_FIELDS}, "analise_id": analysis_id, "posicao": position,
            "criado_em": now, "atualizado_em": now,
        })
    return analysis_id


def functionality_codes(uow: UnitOfWork, analysis_id: int) -> set[str]:
    return {i["codigo"] for i in uow.analysis_items.list_by_analysis(analysis_id) if i["tipo"] == FUNCTIONALITY and i["codigo"]}


def link_stories(uow: UnitOfWork, analysis_id: int, links: list[tuple[list[str], int]]) -> None:
    """Registra nas funcionalidades (FN) da análise as histórias geradas a partir delas."""
    for item in uow.analysis_items.list_by_analysis(analysis_id):
        if item["tipo"] != FUNCTIONALITY:
            continue
        new = [sid for codes, sid in links if item["codigo"] in codes and sid not in item["historia_ids"]]
        if new:
            uow.analysis_items.update(item["id"], {"historia_ids": item["historia_ids"] + new})


REVIEWED_SECTIONS = [
    (AnalysisItemType.USER.value, "Usuários do sistema (use estes papéis como perfil)"),
    (AnalysisItemType.PARTICIPANT.value, "Participantes da reunião (não são perfis de história)"),
    (BUSINESS_REQUIREMENT, "Requisitos de negócio (use como benefício quando as fontes ligarem)"),
    (FUNCTIONALITY, "Funcionalidades já identificadas"),
    (AnalysisItemType.OUT_OF_SCOPE.value, "Fora de escopo (não gere histórias para estes itens)"),
]


def _reviewed_line(item: dict) -> str:
    code = f"{item['codigo']}: " if item["codigo"] else ""
    if item["tipo"] == FUNCTIONALITY and item["complemento"]:
        return f"- {code}({item['complemento']}) {item['texto']}"
    extra = f" — {item['complemento']}" if item["complemento"] else ""
    return f"- {code}{item['texto']}{extra}"


def analysis_text(uow: UnitOfWork, analysis: dict | None) -> str | None:
    """Texto de uma análise para os prompts (ANALISE DO PROJETO / ANÁLISE ATUAL). None se vazia."""
    if not analysis:
        return None
    items = uow.analysis_items.list_by_analysis(analysis["id"])
    lines = [f"Sistema: {analysis['sistema']}"] if analysis["sistema"] else []
    for kind, title in REVIEWED_SECTIONS:
        group = [_reviewed_line(i) for i in items if i["tipo"] == kind]
        if group:
            lines += [f"{title}:", *group]
    return "\n".join(lines) or None


def approved_analysis(uow: UnitOfWork, project_id: int, analysis_id: int | None = None) -> dict:
    """Análise aprovada usada na geração de histórias: a informada ou a aprovada mais recente do projeto."""
    if analysis_id is None:
        analysis = uow.analyses.latest_reviewed(project_id)
        if not analysis:
            raise BusinessValidationError(
                "Revise e aprove a análise das fontes antes de gerar histórias.",
                {"motivo": "sem_analise_aprovada"},
            )
        return analysis
    analysis = uow.analyses.get(analysis_id)
    if not analysis or analysis["projeto_id"] != project_id:
        raise NotFoundError.for_entity("Análise", analysis_id)
    if not analysis["revisada"]:
        raise BusinessValidationError(
            "A análise informada ainda não foi aprovada pelo analista.", {"analise_id": analysis_id}
        )
    return analysis


class AnalysisService:
    """CRUD da análise e de seus itens (revisão humana)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    # -- análise -------------------------------------------------------------
    def _get(self, analysis_id: int) -> dict:
        analysis = self.uow.analyses.get(analysis_id)
        if not analysis:
            raise NotFoundError.for_entity("Análise", analysis_id)
        return analysis

    def _require_no_running_analysis(self, project_id: int) -> None:
        """O job de análise copia a análise atual no início e grava uma nova no fim: edições feitas no meio
        ficariam de fora da nova análise sem aviso. Por isso, nada muda enquanto ele roda."""
        if self.uow.jobs.has_active(project_id, JobType.SOURCE_ANALYSIS.value):
            raise ConflictError(
                "Há uma análise das fontes em andamento neste projeto. Aguarde a conclusão para editar a análise.",
                {"motivo": "analise_em_andamento"},
            )

    def _require_editable(self, analysis: dict) -> None:
        """Só a análise mais recente pode ser editada: edições numa anterior não chegariam às próximas
        atualizações, que partem sempre da mais recente."""
        self._require_no_running_analysis(analysis["projeto_id"])
        latest = self.uow.analyses.latest(analysis["projeto_id"])
        if latest and latest["id"] != analysis["id"]:
            raise ConflictError(
                "Esta análise foi substituída por uma mais recente. Abra a análise atual para editar.",
                {"motivo": "analise_desatualizada", "analise_atual_id": latest["id"]},
            )

    def _present(self, analysis: dict) -> dict:
        return presenters.analysis(analysis, self.uow.analysis_items.list_by_analysis(analysis["id"]))

    def _touch(self, analysis_id: int) -> None:
        self.uow.analyses.update(analysis_id, {"atualizado_em": utc_now_iso()})

    def list(self, project_id: int) -> dict:
        require_project(self.uow, project_id)
        return presenters.listing([self._present(a) for a in self.uow.analyses.list_by_project(project_id)])

    def detail(self, analysis_id: int) -> dict:
        return self._present(self._get(analysis_id))

    def create(self, project_id: int, data: dict, actor: str) -> dict:
        require_project(self.uow, project_id)
        self._require_no_running_analysis(project_id)
        now = utc_now_iso()
        analysis_id = self.uow.analyses.insert({
            "projeto_id": project_id, "origem": "manual", "sistema": data.get("sistema"), "fontes": [],
            "revisada": 0, "criado_em": now, "atualizado_em": now,
        })
        for item in data.get("itens") or []:
            self.add_item(analysis_id, item)
        if data.get("revisada"):
            self._set_reviewed(analysis_id, True, actor)
        return self.detail(analysis_id)

    def _set_reviewed(self, analysis_id: int, reviewed: bool, actor: str) -> None:
        self.uow.analyses.update(analysis_id, {
            "revisada": int(reviewed), "revisada_por": actor if reviewed else None,
            "revisada_em": utc_now_iso() if reviewed else None,
        })

    def update(self, analysis_id: int, data: dict, actor: str) -> dict:
        self._require_editable(self._get(analysis_id))
        if "sistema" in data:
            self.uow.analyses.update(analysis_id, {"sistema": data["sistema"]})
        if data.get("revisada") is not None:
            self._set_reviewed(analysis_id, data["revisada"], actor)
        self._touch(analysis_id)
        return self.detail(analysis_id)

    def delete(self, analysis_id: int) -> None:
        analysis = self._get(analysis_id)
        self._require_no_running_analysis(analysis["projeto_id"])  # análises antigas podem ser excluídas
        self.uow.analyses.delete(analysis_id)

    # -- itens ---------------------------------------------------------------
    def _get_item(self, analysis_id: int, item_id: int) -> dict:
        item = self.uow.analysis_items.get(item_id)
        if not item or item["analise_id"] != analysis_id:
            raise NotFoundError.for_entity("Item de análise", item_id)
        return item

    def _evidences(self, project_id: int, chunk_ids: list[int]) -> list[dict]:
        chunks = {c["id"]: c for c in self.uow.chunks.list_by_ids(chunk_ids)}
        invalid = [cid for cid in chunk_ids if cid not in chunks or chunks[cid]["projeto_id"] != project_id]
        if invalid:
            raise BusinessValidationError("Trechos não encontrados neste projeto.", {"trecho_ids": invalid})
        return [evidence_spec(chunks[cid]) for cid in dict.fromkeys(chunk_ids)]

    def _references(self, analysis_id: int, kind: str, refs: list[str] | None) -> list[str]:
        if not refs:
            return []
        if kind != FUNCTIONALITY:
            raise BusinessValidationError("Somente funcionalidades referenciam requisitos de negócio.")
        known = {i["codigo"] for i in self.uow.analysis_items.list_by_analysis(analysis_id) if i["tipo"] == BUSINESS_REQUIREMENT}
        unknown = sorted(set(refs) - known)
        if unknown:
            raise BusinessValidationError("Requisitos de negócio não encontrados nesta análise.", {"referencias": unknown})
        return list(dict.fromkeys(refs))

    def add_item(self, analysis_id: int, data: dict) -> dict:
        analysis = self._get(analysis_id)
        self._require_editable(analysis)
        kind = data["tipo"]
        now = utc_now_iso()
        item_id = self.uow.analysis_items.insert({
            "analise_id": analysis_id, "tipo": kind,
            "codigo": _next_code(self.uow.analysis_items.list_by_analysis(analysis_id), kind),
            "texto": data["texto"], "complemento": data.get("complemento"),
            "referencias": self._references(analysis_id, kind, data.get("referencias")),
            "evidencias": self._evidences(analysis["projeto_id"], data.get("trecho_ids") or []),
            "historia_ids": [], "origem": "manual", "editado": 0,
            "posicao": self.uow.analysis_items.next_position(analysis_id), "criado_em": now, "atualizado_em": now,
        })
        self._touch(analysis_id)
        return presenters.analysis_item(self.uow.analysis_items.get(item_id))

    def update_item(self, analysis_id: int, item_id: int, data: dict) -> dict:
        analysis = self._get(analysis_id)
        item = self._get_item(analysis_id, item_id)
        self._require_editable(analysis)
        changes: dict[str, Any] = {k: data[k] for k in ("texto", "complemento") if k in data}
        if data.get("referencias") is not None:
            changes["referencias"] = self._references(analysis_id, item["tipo"], data["referencias"])
        if data.get("trecho_ids") is not None:
            changes["evidencias"] = self._evidences(analysis["projeto_id"], data["trecho_ids"])
        if changes:
            self.uow.analysis_items.update(item_id, {**changes, "editado": 1, "atualizado_em": utc_now_iso()})
            self._touch(analysis_id)
        return presenters.analysis_item(self.uow.analysis_items.get(item_id))

    def delete_item(self, analysis_id: int, item_id: int) -> None:
        analysis = self._get(analysis_id)
        item = self._get_item(analysis_id, item_id)
        self._require_editable(analysis)
        self.uow.analysis_items.delete(item_id)
        if item["tipo"] == BUSINESS_REQUIREMENT and item["codigo"]:
            for other in self.uow.analysis_items.list_by_analysis(analysis_id):  # remove referências pendentes
                if item["codigo"] in other["referencias"]:
                    refs = [r for r in other["referencias"] if r != item["codigo"]]
                    self.uow.analysis_items.update(other["id"], {"referencias": refs})
        self._touch(analysis_id)
