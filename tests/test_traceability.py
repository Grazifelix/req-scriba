"""Milestone 2: rastreabilidade de trechos, histórias e critérios; validação estrutural (código) e auditoria
semântica das evidências (VALIDACAO_EVIDENCIAS, LLM + revisão humana)."""

import re

import pytest
from fastapi.testclient import TestClient

from app.core.errors import BusinessValidationError, ExternalServiceError
from app.llm import parsing, prompts
from app.main import create_app
from app.services.criteria import CriteriaService
from app.services.evidence_audit import EvidenceAuditService, run_evidence_audit, start_evidence_audit
from app.services.generation import run_generation, start_generation
from app.services.stories import StoryService
from app.services.traceability import TraceabilityService
from tests.conftest import ensure_approved_analysis
from tests.fakes import default_audit


def generate(container, project_id, audio_ids=(), document_ids=()):
    ensure_approved_analysis(container, project_id)
    with container.uow() as uow:
        job = start_generation(uow, project_id, list(audio_ids), list(document_ids), "ana")
    run_generation(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def declared_story(prompt):
    """Uma história cujo único trecho declara sustentar só a funcionalidade e o perfil."""
    label = re.findall(r"^\[(F\d+)\]", prompt, flags=re.M)[0]
    return {"historias": [{"id": "H1", "perfil": "coordenador pedagógico", "funcionalidade": "emitir o boletim da turma",
                           "beneficio": "acompanhar o desempenho dos alunos",
                           "fontes": [{"trecho_id": label, "afirmacoes_sustentadas": ["perfil", "funcionalidade"],
                                       "citacao": "Precisamos emitir o boletim da turma"}]}]}


@pytest.fixture
def story_id(container, project_id, audio_id):
    container.llm.handlers["GERACAO_US_QUS"] = declared_story
    return generate(container, project_id, [audio_id])["resultado"]["historias_criadas"][0]


def validate(container, story_id):
    with container.uow() as uow:
        return TraceabilityService(uow).validate_story(story_id)


def kinds(report):
    return {(i["tipo"], i["alvo"]["tipo"]) for i in report["problemas"]}


# ---------- modelo de dados ----------

def test_evidence_records_supported_components_as_data(container, story_id):
    with container.uow() as uow:
        [evidence] = uow.evidences.list_by_story(story_id)
        story = StoryService(uow).detail(story_id)
    assert evidence["afirmacoes_sustentadas"] == ["perfil", "funcionalidade"]
    assert story["fontes_audio"][0]["afirmacoes_sustentadas"] == ["perfil", "funcionalidade"]
    criterion = story["criterios_aceitacao"][0]
    assert criterion["fontes"][0]["evidencia_id"] == evidence["id"]  # critério → evidência → trecho


def test_chunk_endpoint_recovers_original_source_and_usages(container, story_id):
    with container.uow() as uow:
        [evidence] = uow.evidences.list_by_story(story_id)
        detail = TraceabilityService(uow).chunk(evidence["trecho_id"])
    assert detail["trecho_id"] == evidence["trecho_id"] and detail["tipo_fonte"] == "audio"
    assert detail["nome_arquivo"] == "reuniao.wav" and detail["url_momento"].endswith("#t=0.0")
    assert detail["descricao"].startswith("áudio reuniao.wav")
    [usage] = detail["usos"]
    assert usage["historia_id"] == story_id and usage["texto_igual_ao_atual"] is True
    assert {c["codigo"] for c in usage["criterios"]} == {"CA01", "CA02"}


def test_traceability_lists_criteria_history_with_versions(container, story_id):
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = lambda p: {"historias": [
        {"id": h, "criterios_aceitacao": [{"dado": "a", "quando": "b", "entao": "c"}]}
        for h in re.findall(r"^\[(H\d+)\]", p, flags=re.M)]}
    with container.uow() as uow:
        CriteriaService(uow, container).generate(story_id, "ana")
        trace = StoryService(uow).traceability(story_id)
    statuses = [(c["codigo"], c["status"], c["versao_historia"]) for c in trace["criterios"]]
    assert statuses == [("CA01", "substituido", 1), ("CA02", "substituido", 1), ("CA01", "vigente", 2)]
    assert [len(v["criterio_ids"]) for v in trace["versoes"]] == [2, 1]


# ---------- validação estrutural ----------

def test_generated_story_is_structurally_valid_but_flags_unsupported_component(container, story_id):
    report = validate(container, story_id)
    assert report["valida"] and report["erros"] == 0
    assert kinds(report) == {("sem_evidencia", "componente")}  # benefício: nenhum trecho declara sustentá-lo
    [issue] = report["problemas"]
    assert issue["alvo"]["id"] == "beneficio" and issue["severidade"] == "aviso"


def test_structural_validation_detects_broken_links(container, story_id):
    with container.uow() as uow:
        [evidence] = uow.evidences.list_by_story(story_id)
        criteria = uow.criteria.list_current(story_id)
        uow.conn.execute("UPDATE trechos_fonte SET texto = texto || ' (corrigido)' WHERE id = ?", (evidence["trecho_id"],))
        uow.criteria.update(criteria[1]["id"], {"codigo": "CA01", "fontes": [{"trecho_id": 999, "evidencia_id": 999,
                                                                              "afirmacoes_sustentadas": ["resultado"]}]})
        uow.evidences.insert({**{k: evidence[k] for k in ("historia_id", "tipo_fonte", "fonte_id", "nome_arquivo",
                                                          "trecho_id", "trecho", "localizacao", "origem", "criado_em")},
                              "afirmacoes_sustentadas": ["telas"]})
        version = uow.versions.list_by_story(story_id)[0]
        uow.versions.update(version["id"], {"evidencia_ids": [evidence["id"], 4242]})
    report = validate(container, story_id)
    assert not report["valida"]
    assert {("citacao_divergente", "evidencia"), ("id_duplicado", "evidencia"), ("afirmacao_invalida", "evidencia"),
            ("id_duplicado", "criterio"), ("referencia_inexistente", "criterio"), ("afirmacao_invalida", "criterio"),
            ("vinculo_invalido", "versao")} <= kinds(report)


def test_deleted_source_is_flagged_but_evidence_copy_is_preserved(container, story_id):
    with container.uow() as uow:
        [evidence] = uow.evidences.list_by_story(story_id)
        uow.conn.execute("DELETE FROM trechos_fonte WHERE id = ?", (evidence["trecho_id"],))
    report = validate(container, story_id)
    assert ("referencia_inexistente", "evidencia") in kinds(report) and report["valida"]  # aviso, não erro
    with container.uow() as uow:
        assert uow.evidences.list_by_story(story_id)[0]["trecho"] == evidence["trecho"]


def test_project_validation_summarizes_by_type(container, project_id, story_id):
    with container.uow() as uow:
        report = TraceabilityService(uow).validate_project(project_id)
    assert report["historias"] == 1 and report["historias_validas"] == 1
    assert report["por_tipo"] == {"sem_evidencia": 1} and report["total"] == 1


# ---------- auditoria semântica ----------

def latest_audit(container, story_id):
    with container.uow() as uow:
        return StoryService(uow).detail(story_id)["auditoria_evidencias"]


def test_generation_audits_components_and_each_criterion_by_its_own_evidence(container, story_id):
    [prompt] = container.llm.prompts_for("VALIDACAO_EVIDENCIAS")
    assert container.llm.schemas["VALIDACAO_EVIDENCIAS"] == prompts.AUDIT_SCHEMA
    assert '- perfil: "coordenador pedagógico" | evidências: F1' in prompt
    assert '- beneficio: "acompanhar o desempenho dos alunos" | evidências: (nenhuma)' in prompt
    assert re.search(r"^- \[H\d+-CA1\] Dado .* \| evidências: F1$", prompt, flags=re.M)
    audit = latest_audit(container, story_id)
    assert audit["origem"] == "geracao" and audit["desatualizada"] is False
    results = {c["componente"]: (c["resultado"], c["origem_resultado"]) for c in audit["componentes"]}
    assert results == {"perfil": ("COMPATIVEL", "llm"), "funcionalidade": ("COMPATIVEL", "llm"),
                       "beneficio": ("SEM_EVIDENCIA", "codigo")}  # sem trecho: decidido pelo código
    assert [c["resultado"] for c in audit["criterios"]] == ["COMPATIVEL", "COMPATIVEL"]
    assert audit["criterios"][0]["evidencias_avaliadas"][0]["evidencia_id"]
    assert audit["resumo"] == {"total_afirmacoes_avaliadas": 5, "compativeis": 4, "parcialmente_compativeis": 0,
                               "incompativeis": 0, "insuficientes": 0, "sem_evidencia": 1}


def test_audit_never_trusts_the_model_beyond_the_linked_evidence(container, story_id):
    def partial(prompt):
        data = default_audit(prompt)
        story = data["auditoria"][0]
        story["componentes"] = [c for c in story["componentes"] if c["componente"] != "perfil"]  # omitido
        story["componentes"][0]["resultado"] = "talvez"
        story["componentes"][0]["evidencias_avaliadas"].append({"trecho_id": "F9", "resultado": "COMPATIVEL"})
        story["criterios_auditados"][0]["resultado"] = "Parcialmente compatível"
        story["criterios_auditados"][0]["componentes_sem_sustentacao"] = ["então"]
        return data
    container.llm.handlers["VALIDACAO_EVIDENCIAS"] = partial
    with container.uow() as uow:
        before = StoryService(uow).detail(story_id)["versao_atual"]
        audit = EvidenceAuditService(uow, container).audit(story_id)
        after = StoryService(uow).detail(story_id)["versao_atual"]
    assert before == after  # a auditoria não altera a história
    components = {c["componente"]: c for c in audit["componentes"]}
    assert components["perfil"]["resultado"] == "INSUFICIENTE" and "não avaliou" in components["perfil"]["problemas"][0]
    assert components["funcionalidade"]["resultado"] == "INSUFICIENTE"  # valor desconhecido nunca vira compatível
    assert any("F9" in p for p in components["funcionalidade"]["problemas"])
    criterion = audit["criterios"][0]
    assert criterion["resultado"] == "PARCIALMENTE_COMPATIVEL" and criterion["componentes_sem_sustentacao"] == ["entao"]


def test_story_without_any_evidence_is_audited_without_calling_the_model(container, project_id, document_id):
    container.llm.handlers["GERACAO_US_QUS"] = lambda p: {"historias": [
        {"funcionalidade": "gerar relatórios", "perfil": "diretor", "beneficio": "decidir", "fontes": ["F42"]}]}
    job = generate(container, project_id, document_ids=[document_id])
    assert not container.llm.prompts_for("VALIDACAO_EVIDENCIAS")
    audit = latest_audit(container, job["resultado"]["historias_criadas"][0])
    assert {c["resultado"] for c in audit["componentes"]} == {"SEM_EVIDENCIA"}


def test_on_demand_audit_failure_saves_nothing_and_old_audit_becomes_outdated(container, story_id):
    container.llm.fail = True
    with container.uow() as uow, pytest.raises(ExternalServiceError):
        EvidenceAuditService(uow, container).audit(story_id)
    container.llm.fail = False
    with container.uow() as uow:
        assert EvidenceAuditService(uow).list(story_id)["total"] == 1  # só a da geração
        StoryService(uow).update(story_id, {"beneficio": "acompanhar as notas"}, "ana")
    assert latest_audit(container, story_id)["desatualizada"] is True


def test_human_review_confirms_or_contests_items(container, story_id):
    audit_id = latest_audit(container, story_id)["auditoria_id"]
    with container.uow() as uow:
        criterion_id = uow.criteria.list_current(story_id)[0]["id"]
        service = EvidenceAuditService(uow)
        service.review(audit_id, "componente:perfil", "confirma", None, "ana")
        service.review(audit_id, "componente:perfil", "contesta", "O trecho não fala do papel.", "bia")
        reviewed = service.review(audit_id, f"criterio:{criterion_id}", "confirma", None, "ana")
        with pytest.raises(BusinessValidationError):
            service.review(audit_id, "componente:telas", "confirma", None, "ana")
    assert [(r["item"], r["decisao"], r["responsavel"]) for r in reviewed["revisoes"]] == [
        ("componente:perfil", "contesta", "bia"), (f"criterio:{criterion_id}", "confirma", "ana")]


def test_audit_stage_is_not_fatal_and_can_be_disabled(container, project_id, audio_id):
    container.llm.handlers["VALIDACAO_EVIDENCIAS"] = lambda p: (_ for _ in ()).throw(ExternalServiceError("fora"))
    job = generate(container, project_id, [audio_id])
    stage = next(s for s in job["resultado"]["etapas"] if s["etapa"] == "auditoria_evidencias")
    assert job["status"] == "concluido" and stage["status"] == "erro"
    container.settings.generation_evidence_audit = False
    with container.uow() as uow:
        uow.conn.execute("DELETE FROM historias")
    job = generate(container, project_id, [audio_id])
    stage = next(s for s in job["resultado"]["etapas"] if s["etapa"] == "auditoria_evidencias")
    assert stage["status"] == "ignorada"


def test_batch_audit_job(container, project_id, story_id):
    with container.uow() as uow:
        job = start_evidence_audit(uow, project_id, [], "ana")
    run_evidence_audit(container, job["id"])
    with container.uow() as uow:
        job = uow.jobs.get(job["id"])
        audits = EvidenceAuditService(uow).list(story_id)
    assert job["status"] == "concluido" and job["resultado"]["total"] == 1
    assert job["resultado"]["resumo"]["compativeis"] == 4
    assert [a["origem"] for a in audits["itens"]] == ["lote", "geracao"]


def test_audit_result_parsing_is_conservative():
    assert parsing.audit_result("Compatível") == "COMPATIVEL"
    assert parsing.audit_result("INCOMPATÍVEL") == "INCOMPATIVEL"
    assert parsing.audit_result("parcial") == "PARCIALMENTE_COMPATIVEL"
    assert parsing.audit_result("sem evidência") == "SEM_EVIDENCIA"
    assert parsing.audit_result("acho que sim") == "INSUFICIENTE"
    assert parsing.audit_result(None) == "INSUFICIENTE"


def test_traceability_api_routes(container, project_id, story_id):
    with TestClient(create_app(container=container), raise_server_exceptions=False) as client:
        assert client.get(f"/api/v1/historias/{story_id}/validacao-estrutural").json()["valida"] is True
        assert client.get(f"/api/v1/projetos/{project_id}/validacao-estrutural").status_code == 200
        audit = client.post(f"/api/v1/historias/{story_id}/auditoria-evidencias")
        assert audit.status_code == 200 and audit.json()["origem"] == "sob_demanda"
        audit_id = audit.json()["auditoria_id"]
        review = client.post(f"/api/v1/auditorias-evidencias/{audit_id}/revisoes",
                             json={"item": "componente:perfil", "decisao": "contesta"}, headers={"X-Usuario": "ana"})
        assert review.status_code == 200 and review.json()["revisoes"][0]["responsavel"] == "ana"
        assert client.post(f"/api/v1/auditorias-evidencias/{audit_id}/revisoes",
                           json={"item": "x", "decisao": "talvez"}).status_code == 422
        assert client.get(f"/api/v1/historias/{story_id}/auditorias-evidencias").json()["total"] == 2
        job = client.post(f"/api/v1/projetos/{project_id}/auditoria-evidencias", json={"historia_ids": [story_id]})
        assert job.status_code == 202 and job.json()["tipo"] == "auditoria_evidencias"
        trecho = client.get(f"/api/v1/historias/{story_id}").json()["fontes_audio"][0]["trecho_id"]
        assert client.get(f"/api/v1/trechos/{trecho}").json()["usos"][0]["historia_id"] == story_id
        assert client.get("/api/v1/trechos/99999").status_code == 404
