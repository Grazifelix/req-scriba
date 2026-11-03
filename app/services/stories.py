"""Histórias de Usuário: criação, revisão humana, evidências e versões (US-05/06, US-16..20)."""

from __future__ import annotations

from typing import Any

from app.core.errors import BusinessValidationError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain import story_format
from app.domain.enums import ChangeOrigin, ChangeType, StoryStatus
from app.services import presenters
from app.services.common import require_project, require_story
from app.services.evidence import NO_SOURCE_CLAIM, add_evidences
from app.services.versioning import create_version, snapshot

TEMPLATE_ELEMENTS = set(story_format.ELEMENTS)


def number_criteria(criteria: list[dict] | None) -> list[dict]:
    """Atribui ids CA01, CA02… preservando título e texto."""
    return [
        {"id": f"CA{i:02d}", "titulo": (c.get("titulo") or f"Critério {i}").strip(), "criterio": c["criterio"].strip()}
        for i, c in enumerate(criteria or [], start=1)
        if (c.get("criterio") or "").strip()
    ]


def merge_missing_info(
    perfil: str | None, funcionalidade: str | None, beneficio: str | None, other_entries: list[dict] | None
) -> list[dict]:
    """Ausências do template (determinísticas) + lacunas adicionais que não sejam do template."""
    missing = story_format.missing_elements(perfil, funcionalidade, beneficio)
    entries = story_format.missing_info_entries(missing)
    for entry in other_entries or []:
        element = (entry.get("elemento") or "").lower().replace("í", "i")
        if element in TEMPLATE_ELEMENTS:
            continue  # elementos do template são decididos pelo conteúdo, não pelo LLM
        if entry not in entries:
            entries.append(entry)
    return entries


def status_for(missing_info: list[dict], current: str | None = None) -> str:
    template_missing = any(e["elemento"] in TEMPLATE_ELEMENTS for e in missing_info)
    if current in (StoryStatus.APPROVED.value, StoryStatus.REJECTED.value):
        return current  # decisão humana é preservada
    return StoryStatus.MISSING_INFO.value if template_missing else StoryStatus.IN_REVIEW.value


def build_content(data: dict) -> dict:
    perfil = story_format.clean_element(data.get("perfil"))
    funcionalidade = story_format.clean_element(data.get("funcionalidade"))
    beneficio = story_format.clean_element(data.get("beneficio"))
    title = (data.get("titulo") or "").strip() or (funcionalidade or "História sem título")[:120].capitalize()
    return {
        "titulo": title, "perfil": perfil, "funcionalidade": funcionalidade, "beneficio": beneficio,
        "historia": story_format.build_story_text(perfil, funcionalidade, beneficio),
        "criterios_aceitacao": number_criteria(data.get("criterios_aceitacao")),
    }


def create_story_record(
    uow: UnitOfWork,
    project_id: int,
    data: dict[str, Any],
    *,
    origin: str,
    change_type: ChangeType,
    change_origin: ChangeOrigin,
    author: str,
    evidence_specs: list[dict] | None = None,
    evidence_origin: str = "extracao",
    suggestion_id: int | None = None,
) -> int:
    """Cria a HU, suas evidências e a versão 1 (US-19 CA01). Usado por geração, manual e atualização."""
    content = build_content(data)
    missing = merge_missing_info(
        content["perfil"], content["funcionalidade"], content["beneficio"], data.get("informacoes_ausentes")
    )
    claims = list(data.get("afirmacoes_sem_evidencia") or [])
    if origin == "ia" and not evidence_specs:
        claims.append(NO_SOURCE_CLAIM)
    now = utc_now_iso()
    story_id = uow.stories.insert({
        "projeto_id": project_id, "codigo": uow.stories.next_code(project_id), **content,
        "status": status_for(missing), "informacoes_ausentes": missing, "afirmacoes_sem_evidencia": claims,
        "origem": origin, "versao_atual": 1, "criado_em": now, "atualizado_em": now,
    })
    add_evidences(uow, story_id, evidence_specs or [], evidence_origin)
    create_version(uow, story_id, change_type, change_origin, author, suggestion_id)
    return story_id


def apply_content_change(uow: UnitOfWork, story: dict, changes: dict) -> dict | None:
    """Aplica mudanças de conteúdo. Retorna o novo conteúdo ou None se nada mudou."""
    merged = {**snapshot(story), **changes}
    content = build_content(merged)
    if content == snapshot(story):
        return None
    missing = merge_missing_info(
        content["perfil"], content["funcionalidade"], content["beneficio"], story["informacoes_ausentes"]
    )
    uow.stories.update(story["id"], {
        **content, "informacoes_ausentes": missing, "status": status_for(missing, story["status"]),
        "atualizado_em": utc_now_iso(),
    })
    return content


class StoryService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def detail(self, story_id: int) -> dict:
        story = require_story(self.uow, story_id)
        return presenters.story(
            story,
            self.uow.evidences.list_by_story(story_id),
            self.uow.versions.list_by_story(story_id),
            self.uow.evaluations.latest(story_id),
            self.uow.suggestions.count_pending_for_story(story_id),
        )

    def list(self, project_id: int, status: str | None = None) -> dict:
        require_project(self.uow, project_id)
        stories = self.uow.stories.list_by_project(project_id, status)
        return presenters.listing([presenters.story_summary(s) for s in stories])

    def create_manual(self, project_id: int, data: dict, author: str) -> dict:
        require_project(self.uow, project_id)
        if not story_format.clean_element(data.get("funcionalidade")):
            raise BusinessValidationError("Informe ao menos a funcionalidade da história.")
        story_id = create_story_record(
            self.uow, project_id, data, origin="manual", change_type=ChangeType.MANUAL,
            change_origin=ChangeOrigin.MANUAL_CREATION, author=author,
        )
        return self.detail(story_id)

    def update(self, story_id: int, changes: dict, author: str) -> dict:
        """US-06 CA01 / US-19 CA02: edição manual gera nova versão (somente se algo mudou)."""
        story = require_story(self.uow, story_id)
        changes = dict(changes)
        text = changes.pop("historia", None)
        if text is not None:
            parsed = story_format.parse_story_text(text)
            if parsed is None:
                raise BusinessValidationError(
                    "O texto da história deve seguir o padrão 'Como [perfil], quero [funcionalidade], para [benefício]'."
                )
            changes = {**parsed, **changes}  # campos explícitos têm precedência sobre o texto
        if apply_content_change(self.uow, story, changes):
            create_version(self.uow, story_id, ChangeType.MANUAL, ChangeOrigin.MANUAL_EDIT, author)
        return self.detail(story_id)

    def set_status(self, story_id: int, status: StoryStatus) -> dict:
        """US-06 CA02/CA03: aprovação e desaprovação (não criam versão de conteúdo)."""
        require_story(self.uow, story_id)
        self.uow.stories.update(story_id, {"status": status.value, "atualizado_em": utc_now_iso()})
        return self.detail(story_id)

    def evidences(self, story_id: int, version_number: int | None = None) -> dict:
        """US-17: evidências atuais ou de uma versão específica (US-18 CA03)."""
        require_story(self.uow, story_id)
        if version_number is None:
            items = self.uow.evidences.list_by_story(story_id)
        else:
            items = self.uow.evidences.list_by_ids(self._version(story_id, version_number)["evidencia_ids"])
        audio, documents = presenters.split_evidences(items)
        return {
            "historia_id": story_id, "versao": version_number, "fontes_audio": audio, "fontes_documentos": documents,
            "possui_suporte_documental": bool(items), **presenters.listing([presenters.evidence(e) for e in items]),
        }

    def traceability(self, story_id: int) -> dict:
        """US-17 CA04 / US-18: evidências por versão, novas evidências e navegação sequencial."""
        story = require_story(self.uow, story_id)
        evidences = {e["id"]: presenters.evidence(e) for e in self.uow.evidences.list_by_story(story_id)}
        versions, previous_ids = [], set()
        for v in self.uow.versions.list_by_story(story_id):
            ids = v["evidencia_ids"]
            versions.append({
                "numero": v["numero"], "origem_alteracao": v["origem_alteracao"], "data_hora": v["criado_em"],
                "evidencias": [evidences[i] for i in ids if i in evidences],
                "novas_evidencia_ids": [i for i in ids if i not in previous_ids],
            })
            previous_ids = set(ids)
        ordered = list(evidences.values())
        navigation = [
            {
                "evidencia_id": e["evidencia_id"], "indice": i,
                "anterior": ordered[i - 1]["evidencia_id"] if i > 0 else None,
                "proxima": ordered[i + 1]["evidencia_id"] if i < len(ordered) - 1 else None,
            }
            for i, e in enumerate(ordered)
        ]
        return {
            "historia_id": story_id, "codigo": story["codigo"], "versao_atual": story["versao_atual"],
            "possui_suporte_documental": bool(ordered), "afirmacoes_sem_evidencia": story["afirmacoes_sem_evidencia"],
            "versoes": versions, "navegacao": navigation, "total_evidencias": len(ordered),
        }

    def _version(self, story_id: int, number: int) -> dict:
        version = self.uow.versions.get_by_number(story_id, number)
        if not version:
            raise NotFoundError(f"Versão {number} da história {story_id} não encontrada.")
        return version

    def versions(self, story_id: int) -> dict:
        """US-20 CA01/CA03: ordem cronológica; com uma só versão, mostra o registro de criação."""
        require_story(self.uow, story_id)
        return {"historia_id": story_id, **presenters.listing(
            [presenters.version(v) for v in self.uow.versions.list_by_story(story_id)]
        )}

    def version(self, story_id: int, number: int) -> dict:
        require_story(self.uow, story_id)
        return presenters.version(self._version(story_id, number))
