"""Milestone 5: critérios de aceitação ligados à versão da história; desatualização quando a história muda,
confirmação, regeneração individual e em lote (proposta para histórias aprovadas)."""

import re

import pytest
from fastapi.testclient import TestClient

from app.core.errors import BusinessValidationError
from app.domain.enums import StoryStatus
from app.main import create_app
from app.services.criteria import CriteriaService, run_criteria_generation, start_criteria_generation
from app.services.stories import StoryService
from app.services.updates import UpdateService
from tests.test_services_updates import analyze, approved_story, new_doc, story, suggestions  # noqa: F401

NEW_CRITERION = {"dado": "um bimestre encerrado", "quando": "o coordenador emitir o boletim",
                 "entao": "o boletim sai em PDF"}


def one_new_criterion(prompt):
    return {"historias": [{"id": h, "criterios_aceitacao": [
        {**NEW_CRITERION, "fontes": [{"trecho_id": "F1", "afirmacoes_sustentadas": ["entao"]}]}]}
        for h in re.findall(r"^\[(H\d+)\]", prompt, flags=re.M)]}


def criteria(container, story_id):
    return story(container, story_id)["criterios_aceitacao"]


def edit(container, story_id, changes):
    with container.uow() as uow:
        return StoryService(uow).update(story_id, changes, "ana")


@pytest.fixture
def unapproved(container, approved_story):  # noqa: F811 - mesma história, volta para revisão
    with container.uow() as uow:
        StoryService(uow).set_status(approved_story, StoryStatus.IN_REVIEW)
    return approved_story


# ---------- desatualização ----------

def test_modification_marks_previous_criteria_outdated_with_ai_hint(container, project_id, approved_story):  # noqa: F811
    before = criteria(container, approved_story)
    analyze(container, project_id, [new_doc(container, project_id)])
    [proposal] = suggestions(container, project_id, approved_story)
    with container.uow() as uow:
        UpdateService(uow).approve(proposal["id"], "ana")
    after = criteria(container, approved_story)
    assert [c["criterio_id"] for c in after] == [c["criterio_id"] for c in before]  # preservados, mesmo id
    assert {c["situacao"] for c in after} == {"desatualizado"}
    assert all(c["desatualizado_na_versao"] == 2 and c["validado_na_versao"] == 1 for c in after)
    assert "funcionalidade" in after[0]["motivo_situacao"] and "A IA indicou revisar" in after[0]["motivo_situacao"]
    versions = story(container, approved_story)["versoes"]
    assert versions[1]["criterio_ids"] == versions[0]["criterio_ids"]  # vínculo por versão preservado


def test_manual_component_edit_outdates_but_reviewed_list_stays_valid(container, unapproved):
    edit(container, unapproved, {"beneficio": "acompanhar as notas"})
    assert {c["situacao"] for c in criteria(container, unapproved)} == {"desatualizado"}
    current = criteria(container, unapproved)
    payload = [{"titulo": c["titulo"], "criterio": c["criterio"]} for c in current]
    edit(container, unapproved, {"funcionalidade": "emitir o boletim bimestral", "criterios_aceitacao": payload})
    after = criteria(container, unapproved)
    assert {(c["situacao"], c["validado_na_versao"]) for c in after} == {("valido", 3)}  # o analista enviou a lista


def test_criteria_only_change_does_not_outdate(container, unapproved):
    current = criteria(container, unapproved)
    edit(container, unapproved, {"criterios_aceitacao": [{"criterio": current[0]["criterio"]},
                                                         {"criterio": "Dado x, quando y, então z."}]})
    assert {c["situacao"] for c in criteria(container, unapproved)} == {"valido"}


def test_confirm_marks_valid_for_current_version_without_new_version(container, unapproved):
    edit(container, unapproved, {"beneficio": "acompanhar as notas"})
    first = criteria(container, unapproved)[0]
    with container.uow() as uow:
        result = CriteriaService(uow, container).confirm(unapproved, first["criterio_id"])
    confirmed = next(c for c in result["criterios_aceitacao"] if c["criterio_id"] == first["criterio_id"])
    assert confirmed["situacao"] == "valido" and confirmed["validado_na_versao"] == 2 and confirmed["motivo_situacao"] is None
    assert result["versao_atual"] == 2


# ---------- regeneração ----------

def test_regeneration_replaces_only_outdated_and_keeps_confirmed(container, unapproved):
    edit(container, unapproved, {"beneficio": "acompanhar as notas"})
    kept, outdated = criteria(container, unapproved)
    with container.uow() as uow:
        CriteriaService(uow, container).confirm(unapproved, kept["criterio_id"])
    container.llm.calls.clear()
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = one_new_criterion
    with container.uow() as uow:
        result = CriteriaService(uow, container).generate(unapproved, "ana")  # escopo auto → desatualizados
    [prompt] = container.llm.prompts_for("GERACAO_CRITERIOS_ACEITACAO")
    assert f"Critérios já validados (continuam valendo; não repita nem reescreva):\n- {kept['criterio']}" in prompt
    assert outdated["criterio"] not in prompt.split("HISTÓRIAS:")[1].split("### TAREFA")[0]
    ids = [c["criterio_id"] for c in result["criterios_aceitacao"]]
    assert ids[0] == kept["criterio_id"] and outdated["criterio_id"] not in ids
    new = result["criterios_aceitacao"][1]
    assert new["entao"] == "o boletim sai em PDF" and new["versao_historia"] == 3 and new["situacao"] == "valido"
    with container.uow() as uow:
        replaced = uow.criteria.get(outdated["criterio_id"])
        evidences = uow.evidences.list_by_story(unapproved)
    assert replaced["status"] == "substituido" and replaced["substituido_na_versao"] == 3  # preservado
    assert new["fontes"][0]["evidencia_id"] in {e["id"] for e in evidences}


def test_individual_regeneration_by_criterion_id(container, unapproved):
    first, second = criteria(container, unapproved)
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = one_new_criterion
    with container.uow() as uow:
        result = CriteriaService(uow, container).generate(unapproved, "ana", criterion_ids=[second["criterio_id"]])
    assert [c["criterio_id"] for c in result["criterios_aceitacao"]][0] == first["criterio_id"]
    assert len(result["criterios_aceitacao"]) == 2 and second["criterio_id"] not in {
        c["criterio_id"] for c in result["criterios_aceitacao"]}
    with container.uow() as uow, pytest.raises(BusinessValidationError):
        CriteriaService(uow, container).generate(unapproved, "ana", criterion_ids=[9999])
    with container.uow() as uow, pytest.raises(BusinessValidationError) as exc:
        CriteriaService(uow, container).generate(unapproved, "ana", "desatualizados")
    assert exc.value.details["motivo"] == "sem_desatualizados"


def test_approved_story_regeneration_is_a_proposal_applied_on_approval(container, project_id, approved_story):  # noqa: F811
    analyze(container, project_id, [new_doc(container, project_id)])
    [modification] = suggestions(container, project_id, approved_story)
    with container.uow() as uow:
        UpdateService(uow).approve(modification["id"], "ana")  # v2: critérios desatualizados
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = one_new_criterion
    with container.uow() as uow:
        result = CriteriaService(uow, container).generate(approved_story, "ana")
    assert result["versao_atual"] == 2 and {c["situacao"] for c in result["criterios_aceitacao"]} == {"desatualizado"}
    with container.uow() as uow:
        proposal = UpdateService(uow).get(result["proposta_criterios_id"])
    assert proposal["tipo"] == "revisao_criterios" and proposal["versao_base"] == 2
    assert len(proposal["detalhes"]["criterios_substituidos"]) == 2 and proposal["detalhes"]["criterios_mantidos"] == []
    assert [c["criterio"] for c in proposal["conteudo_proposto"]["criterios_aceitacao"]] == [
        "Dado um bimestre encerrado, quando o coordenador emitir o boletim, então o boletim sai em PDF."]
    with container.uow() as uow:
        decided = UpdateService(uow).approve(proposal["id"], "ana")
    after = story(container, approved_story)
    assert decided["versao_aplicada"] == 3 and after["status"] == "aprovada"
    [current] = after["criterios_aceitacao"]
    assert current["situacao"] == "valido" and current["versao_historia"] == 3
    assert after["versoes"][-1]["criterio_ids"] == [current["criterio_id"]]


def test_batch_regenerates_only_stories_that_need_it(container, project_id, unapproved):
    edit(container, unapproved, {"beneficio": "acompanhar as notas"})  # US-001 com critérios desatualizados
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = one_new_criterion
    with container.uow() as uow:
        job = start_criteria_generation(uow, project_id, [], False, "ana")
    run_criteria_generation(container, job["id"])
    with container.uow() as uow:
        job = uow.jobs.get(job["id"])
    assert unapproved in job["resultado"]["historias_atualizadas"]
    assert {c["situacao"] for c in criteria(container, unapproved)} == {"valido"}


def test_new_story_from_approved_need_can_receive_criteria(container, project_id, approved_story):  # noqa: F811
    analyze(container, project_id, [new_doc(container, project_id)])
    [need] = [s for s in suggestions(container, project_id) if s["tipo"] == "nova_necessidade"]
    with container.uow() as uow:
        created = UpdateService(uow).approve(need["id"], "ana")["historia_criada_id"]
        result = CriteriaService(uow, container).generate(created, "ana")
    assert result["criterios_aceitacao"] and result["versao_atual"] == 2
    assert {c["versao_historia"] for c in result["criterios_aceitacao"]} == {2}


def test_criteria_version_api(container, unapproved):
    edit(container, unapproved, {"beneficio": "acompanhar as notas"})
    first = criteria(container, unapproved)[0]
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = one_new_criterion
    with TestClient(create_app(container=container), raise_server_exceptions=False) as client:
        confirmed = client.post(f"/api/v1/historias/{unapproved}/criterios/{first['criterio_id']}/confirmar")
        assert confirmed.status_code == 200
        assert confirmed.json()["criterios_aceitacao"][0]["situacao"] == "valido"
        assert client.post(f"/api/v1/historias/{unapproved}/criterios/999/confirmar").status_code == 404
        regenerated = client.post(f"/api/v1/historias/{unapproved}/criterios/gerar", json={"escopo": "desatualizados"})
        assert regenerated.status_code == 200 and len(regenerated.json()["criterios_aceitacao"]) == 2
        none_left = client.post(f"/api/v1/historias/{unapproved}/criterios/gerar", json={"escopo": "desatualizados"})
        assert none_left.status_code == 422
