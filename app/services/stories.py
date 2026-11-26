"""Histórias de Usuário: criação, revisão humana, evidências e versões (US-05/06, US-16..20)."""

from __future__ import annotations

from typing import Any

from app.core.container import Container
from app.core.errors import BusinessValidationError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain import story_format
from app.domain.criteria import CRITERION_EXTRA_FIELDS, criteria_content, number_criteria
from app.domain.text import normalize
from app.domain.enums import ChangeOrigin, ChangeType, StoryStatus
from app.llm import parsing, prompts
from app.services import presenters
from app.services.common import require_project, require_story
from app.services.evidence import NO_SOURCE_CLAIM, add_evidences
from app.services.retrieval import describe_chunk
from app.services.criteria_store import sync_criteria
from app.services.versioning import create_version, same_content, snapshot

TEMPLATE_ELEMENTS = set(story_format.ELEMENTS)
MAX_SPLIT_EVIDENCE_BLOCKS = 8
MISSING_CRITERIA = {
    "elemento": "criterios_aceitacao",
    "descricao": "Não houve base nas fontes para critérios de aceitação desta história, criada na divisão de outra.",
}


__all__ = ["CRITERION_EXTRA_FIELDS", "number_criteria"]  # reexportados (usados pelos serviços de critérios)


def keep_traceability(incoming: list[dict], current: list[dict]) -> list[dict]:
    """Edição manual da lista de critérios: o cliente reenvia só título e texto. Um critério cujo texto não mudou
    mantém Dado/Quando/Então, fontes e sinalizações; um critério com texto alterado ou novo perde as fontes
    (elas sustentavam o texto anterior, não o novo)."""
    by_text = {normalize(c.get("criterio")): c for c in current or []}
    result = []
    for criterion in incoming:
        previous = by_text.get(normalize(criterion.get("criterio")))
        extras = {k: previous[k] for k in CRITERION_EXTRA_FIELDS if previous and k in previous}
        result.append({**extras, **{k: v for k, v in criterion.items() if v is not None}})
    return result


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
    criteria = content.pop("criterios_aceitacao")
    story_id = uow.stories.insert({
        "projeto_id": project_id, "codigo": uow.stories.next_code(project_id), **content,
        "status": status_for(missing), "informacoes_ausentes": missing, "afirmacoes_sem_evidencia": claims,
        "origem": origin, "versao_atual": 1, "criado_em": now, "atualizado_em": now,
    })
    add_evidences(uow, story_id, evidence_specs or [], evidence_origin)  # antes dos critérios: eles citam evidências
    sync_criteria(uow, story_id, criteria, 1)
    create_version(uow, story_id, change_type, change_origin, author, suggestion_id)
    return story_id


def apply_content_change(uow: UnitOfWork, story: dict, changes: dict) -> dict | None:
    """Aplica mudanças de conteúdo. Retorna o novo conteúdo ou None se nada mudou.

    Critérios alterados são reconciliados na tabela de critérios como pertencentes à próxima versão, que o
    chamador cria logo em seguida (``create_version``)."""
    merged = {**snapshot(story), **changes}
    content = build_content(merged)
    if same_content(content, story):
        return None
    missing = merge_missing_info(
        content["perfil"], content["funcionalidade"], content["beneficio"], story["informacoes_ausentes"]
    )
    fields = {k: v for k, v in content.items() if k != "criterios_aceitacao"}
    uow.stories.update(story["id"], {
        **fields, "informacoes_ausentes": missing, "status": status_for(missing, story["status"]),
        "atualizado_em": utc_now_iso(),
    })
    if criteria_content(content["criterios_aceitacao"]) != snapshot(story)["criterios_aceitacao"]:
        sync_criteria(uow, story["id"], content["criterios_aceitacao"], story["versao_atual"] + 1)
    return content


def _parse_or_fail(text: str) -> dict:
    parsed = story_format.parse_story_text(text)
    if parsed is None:
        raise BusinessValidationError(
            "O texto da história deve seguir o padrão 'Como [perfil], quero [funcionalidade], para [benefício]'."
        )
    return parsed


class StoryService:
    def __init__(self, uow: UnitOfWork, container: Container | None = None) -> None:
        self.uow = uow
        self.container = container  # só a divisão de história usa o LLM

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
        rewrites = self.uow.evaluations.latest_rewrites(project_id)
        return presenters.listing([presenters.story_summary(s, rewrites.get(s["id"])) for s in stories])

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
        if changes.get("criterios_aceitacao") is not None:
            changes["criterios_aceitacao"] = keep_traceability(changes["criterios_aceitacao"], story["criterios_aceitacao"])
        text = changes.pop("historia", None)
        if text is not None:
            changes = {**_parse_or_fail(text), **changes}  # campos explícitos têm precedência sobre o texto
        if apply_content_change(self.uow, story, changes):
            create_version(self.uow, story_id, ChangeType.MANUAL, ChangeOrigin.MANUAL_EDIT, author)
        return self.detail(story_id)

    def apply_rewrite(self, story_id: int, text: str, author: str) -> dict:
        """Aplica a reescrita da avaliação. Se ela divide a história ("H1: …; H2: …"), a primeira parte
        substitui a história atual e as demais viram histórias novas com as mesmas evidências. Na divisão,
        o LLM escreve critérios de aceitação próprios para cada parte."""
        story = require_story(self.uow, story_id)
        texts = story_format.split_story_texts(text)
        parsed = [_parse_or_fail(part) for part in texts]
        evidences = self.uow.evidences.list_by_story(story_id)  # herdadas, com a origem de cada uma
        if len(parsed) > 1:
            for fields, criteria in zip(parsed, self._split_criteria(story, texts, evidences)):
                fields["criterios_aceitacao"] = criteria
            if not parsed[0]["criterios_aceitacao"]:
                del parsed[0]["criterios_aceitacao"]  # sem base para novos: a original mantém os atuais
        if apply_content_change(self.uow, story, parsed[0]):
            create_version(self.uow, story_id, ChangeType.MANUAL, ChangeOrigin.MANUAL_EDIT, author)
        created = [
            create_story_record(
                self.uow, story["projeto_id"],
                {**fields, "informacoes_ausentes": [] if fields["criterios_aceitacao"] else [MISSING_CRITERIA]},
                origin="manual", change_type=ChangeType.MANUAL, change_origin=ChangeOrigin.MANUAL_CREATION,
                author=author, evidence_specs=evidences,
            )
            for fields in parsed[1:]
        ]
        return {"historia": self.detail(story_id), "historias_criadas": [self.detail(i) for i in created]}

    def _split_criteria(self, story: dict, texts: list[str], evidences: list[dict]) -> list[list[dict]]:
        if self.container is None:
            raise RuntimeError("A divisão de história exige o container com o LLM.")
        blocks = [
            {"rotulo": f"E{i}", "descricao": describe_chunk({**e, "texto": e["trecho"]}), "texto": e["trecho"]}
            for i, e in enumerate(evidences[:MAX_SPLIT_EVIDENCE_BLOCKS], start=1)
        ]
        data = self.container.llm.generate_json(prompts.SYSTEM, prompts.split_criteria_prompt(story, texts, blocks))
        return parsing.normalize_split_criteria(data, len(texts))

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
