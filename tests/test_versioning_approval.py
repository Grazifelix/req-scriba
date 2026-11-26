"""Milestone 4: versionamento (fontes que motivaram cada versão, status, comparação) e gestão de propostas
(PENDENTE, APROVADA, REJEITADA, CANCELADA; proposta desatualizada; duplicidade antes do código definitivo)."""

import pytest
from fastapi.testclient import TestClient

from app.core.errors import BusinessValidationError, ConflictError
from app.domain.enums import StoryStatus
from app.main import create_app
from app.services.stories import StoryService
from app.services.updates import UpdateService
from tests.test_services_updates import analyze, approved_story, new_doc, story, suggestions  # noqa: F401


def proposals(container, project_id):
    items = suggestions(container, project_id)
    modification = next(s for s in items if s["tipo"] == "modificacao")
    need = next(s for s in items if s["tipo"] == "nova_necessidade")
    return modification, need


@pytest.fixture
def pending(container, project_id, approved_story):  # noqa: F811
    analyze(container, project_id, [new_doc(container, project_id)])
    return proposals(container, project_id)


# ---------- versionamento ----------

def test_versions_record_motivating_sources_and_status(container, project_id, approved_story, pending):  # noqa: F811
    modification, _ = pending
    with container.uow() as uow:
        UpdateService(uow).approve(modification["id"], "ana")
        versions = StoryService(uow).versions(approved_story)["itens"]
    v1, v2 = versions
    assert [s["nome_arquivo"] for s in v1["fontes"]] == ["reuniao.wav"]  # geração: fonte de origem
    assert [s["nome_arquivo"] for s in v2["fontes"]] == ["ata2.txt"]  # a fonte nova motivou a v2
    assert v2["fontes"][0]["evidencia_ids"] and v2["sugestao_atualizacao_id"] == modification["id"]
    assert v1["status_historia"] == "em_revisao" and v2["status_historia"] == "aprovada"
    assert v2["status_historia_rotulo"] == "Aprovada"


def test_manual_edit_version_has_no_motivating_source(container, approved_story):  # noqa: F811
    with container.uow() as uow:
        StoryService(uow).update(approved_story, {"beneficio": "acompanhar as notas"}, "ana")
        last = StoryService(uow).versions(approved_story)["itens"][-1]
    assert last["fontes"] == [] and last["origem_alteracao"] == "edicao_manual"


def test_compare_versions_reports_fields_criteria_evidence_and_changes(container, project_id, approved_story, pending):  # noqa: F811
    modification, _ = pending
    with container.uow() as uow:
        UpdateService(uow).approve(modification["id"], "ana")
        StoryService(uow).update(approved_story, {"criterios_aceitacao": [{"criterio": "Dado x, quando y, então z."}]}, "bia")
        comparison = StoryService(uow).compare_versions(approved_story, 1, 3)
        default = StoryService(uow).compare_versions(approved_story)
    assert comparison["de"] == 1 and comparison["para"] == 3
    assert "funcionalidade" in comparison["conteudo"]["campos_alterados"]
    assert "+PDF," in comparison["conteudo"]["diff_texto"]  # diff por palavra
    criteria = comparison["criterios"]
    assert criteria["registrados"] and [c["criterio"] for c in criteria["adicionados"]] == ["Dado x, quando y, então z."]
    assert {c["codigo"] for c in criteria["removidos"]} == {"CA01", "CA02"} and criteria["mantidos"] == []
    assert [e["nome_arquivo"] for e in comparison["evidencias"]["adicionadas"]] == ["ata2.txt"]
    assert [(v["numero"], v["responsavel"]) for v in comparison["alteracoes"]] == [(2, "ana"), (3, "bia")]
    assert (default["de"], default["para"]) == (2, 3)
    with container.uow() as uow, pytest.raises(BusinessValidationError):
        StoryService(uow).compare_versions(approved_story, 3, 2)


# ---------- estados das propostas ----------

def test_cancel_keeps_proposal_with_reason_and_blocks_decisions(container, pending):
    modification, _ = pending
    with container.uow() as uow:
        cancelled = UpdateService(uow).cancel(modification["id"], "ana", "Fonte enviada por engano.")
    assert cancelled["status"] == "cancelada" and cancelled["status_rotulo"] == "Cancelada"
    assert cancelled["motivo_decisao"] == "Fonte enviada por engano." and cancelled["decidido_por"] == "ana"
    for action in ("approve", "reject", "cancel"):
        with container.uow() as uow, pytest.raises(ConflictError):
            getattr(UpdateService(uow), action)(modification["id"], "ana")


def test_reject_records_reason(container, pending):
    _, need = pending
    with container.uow() as uow:
        rejected = UpdateService(uow).reject(need["id"], "ana", "Fora do escopo.")
    assert rejected["status"] == "rejeitada" and rejected["motivo_decisao"] == "Fora do escopo."


def test_rejecting_story_cancels_its_pending_proposals(container, project_id, approved_story, pending):  # noqa: F811
    modification, need = pending
    with container.uow() as uow:
        StoryService(uow).set_status(approved_story, StoryStatus.REJECTED, "ana")
        after = UpdateService(uow).get(modification["id"])
        other = UpdateService(uow).get(need["id"])
    assert after["status"] == "cancelada" and after["motivo_decisao"] == "História desaprovada."
    assert other["status"] == "pendente"  # nova necessidade não pertence à história desaprovada


def test_outdated_proposal_requires_confirmation_and_preserves_later_edits(container, approved_story, pending):  # noqa: F811
    modification, _ = pending
    with container.uow() as uow:
        StoryService(uow).update(approved_story, {"beneficio": "acompanhar as notas"}, "bia")  # v2, depois da proposta
        assert UpdateService(uow).get(modification["id"])["desatualizada"] is True
    with container.uow() as uow, pytest.raises(ConflictError) as exc:
        UpdateService(uow).approve(modification["id"], "ana")
    details = exc.value.details
    assert details["motivo"] == "proposta_desatualizada" and (details["versao_base"], details["versao_atual"]) == (1, 2)
    assert set(details["campos_propostos"]) == {"titulo", "funcionalidade"}
    with container.uow() as uow:
        decided = UpdateService(uow).approve(modification["id"], "ana", accept_outdated=True)
    after = story(container, approved_story)
    assert decided["status"] == "aprovada" and decided["versao_aplicada"] == 3
    assert after["funcionalidade"] == "emitir o boletim da turma em PDF"  # campo da proposta aplicado
    assert after["beneficio"] == "acompanhar as notas"  # edição posterior preservada
    assert len(after["criterios_aceitacao"]) == 2  # critérios não fazem parte da proposta: intactos


def test_second_proposal_becomes_outdated_after_first_is_applied(container, project_id, approved_story, pending):  # noqa: F811
    first, _ = pending
    analyze(container, project_id, [new_doc(container, project_id, name="ata3.txt")])
    second = [s for s in suggestions(container, project_id, approved_story) if s["id"] != first["id"]][0]
    with container.uow() as uow:
        UpdateService(uow).approve(first["id"], "ana")
        assert UpdateService(uow).get(second["id"])["desatualizada"] is True
    with container.uow() as uow, pytest.raises(ConflictError):
        UpdateService(uow).approve(second["id"], "ana")


def test_new_story_is_checked_against_current_backlog_before_definitive_code(container, project_id, pending):
    _, need = pending
    with container.uow() as uow:
        StoryService(uow).create_manual(project_id, {"perfil": "responsável", "funcionalidade":
                                                     "receber notificações de faltas por e-mail"}, "ana")
    with container.uow() as uow, pytest.raises(ConflictError) as exc:
        UpdateService(uow).approve(need["id"], "ana")
    assert exc.value.details["motivo"] == "possivel_duplicidade" and exc.value.details["codigo"] == "US-003"
    with container.uow() as uow:
        decided = UpdateService(uow).approve(need["id"], "ana", accept_duplicate=True)
        created = StoryService(uow).detail(decided["historia_criada_id"])
    assert created["codigo"] == "US-004"


def test_list_filters_by_story_and_status(container, project_id, approved_story, pending):  # noqa: F811
    modification, _ = pending
    with container.uow() as uow:
        UpdateService(uow).cancel(modification["id"], "ana")
        by_story = UpdateService(uow).list(project_id, story_id=approved_story)["itens"]
        cancelled = UpdateService(uow).list(project_id, "cancelada")["itens"]
    assert [s["id"] for s in by_story] == [modification["id"]] and [s["id"] for s in cancelled] == [modification["id"]]


def test_versioning_and_approval_api(container, project_id, approved_story, pending):  # noqa: F811
    modification, need = pending
    with TestClient(create_app(container=container), raise_server_exceptions=False) as client:
        client.patch(f"/api/v1/historias/{approved_story}", json={"beneficio": "acompanhar as notas"})
        stale = client.post(f"/api/v1/sugestoes-atualizacao/{modification['id']}/aprovar")
        assert stale.status_code == 409 and stale.json()["erro"]["detalhes"]["motivo"] == "proposta_desatualizada"
        ok = client.post(f"/api/v1/sugestoes-atualizacao/{modification['id']}/aprovar",
                         json={"aceitar_desatualizada": True}, headers={"X-Usuario": "ana"})
        assert ok.status_code == 200 and ok.json()["versao_aplicada"] == 3
        cancel = client.post(f"/api/v1/sugestoes-atualizacao/{need['id']}/cancelar", json={"motivo": "duplicada"})
        assert cancel.status_code == 200 and cancel.json()["status"] == "cancelada"
        assert client.post(f"/api/v1/sugestoes-atualizacao/{need['id']}/cancelar").status_code == 409
        listed = client.get(f"/api/v1/projetos/{project_id}/sugestoes-atualizacao",
                            params={"status": "cancelada"}).json()
        assert listed["total"] == 1
        comparison = client.get(f"/api/v1/historias/{approved_story}/versoes/comparacao").json()
        assert (comparison["de"], comparison["para"]) == (2, 3)
        assert client.get(f"/api/v1/historias/{approved_story}/versoes/comparacao", params={"de": 2, "para": 9}).status_code == 404
        version = client.get(f"/api/v1/historias/{approved_story}/versoes/3").json()
        assert version["fontes"][0]["nome_arquivo"] == "ata2.txt" and version["status_historia"] == "aprovada"
