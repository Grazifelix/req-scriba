"""Milestone 1: geração de US sem critérios (GERACAO_US_QUS), critérios gerados à parte
(GERACAO_CRITERIOS_ACEITACAO), validação estrutural, etapas do pipeline e execução independente."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ConflictError, ExternalServiceError
from app.domain.enums import StoryStatus
from app.domain.traceability import CRITERION_WITHOUT_EVIDENCE
from app.llm import prompts
from app.main import create_app
from app.services.criteria import FAILED, CriteriaService, run_criteria_generation, start_criteria_generation
from app.services.generation import NOT_GENERATED, run_generation, start_generation
from app.services.stories import StoryService
from tests.conftest import ensure_approved_analysis
from tests.fakes import default_criteria

STAGES = ["preparacao", "geracao_us", "consolidacao_us", "validacao_us", "geracao_criterios",
          "validacao_criterios", "persistencia"]


def generate(container, project_id, audio_ids=(), document_ids=()):
    ensure_approved_analysis(container, project_id)
    with container.uow() as uow:
        job = start_generation(uow, project_id, list(audio_ids), list(document_ids), "ana")
    run_generation(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def detail(container, story_id):
    with container.uow() as uow:
        return StoryService(uow).detail(story_id)


def labels_in(prompt):
    return re.findall(r"^\[(F\d+)\]", prompt, flags=re.M)


def one_story(fontes, **extra):
    """Handler de GERACAO_US_QUS com uma história completa citando ``fontes``."""
    def handler(prompt):
        return {"historias": [{"id": "H1", "titulo": "Emitir boletim", "perfil": "coordenador pedagógico",
                               "funcionalidade": "emitir o boletim da turma",
                               "beneficio": "acompanhar o desempenho dos alunos", "fontes": fontes, **extra}]}
    return handler


# ---------- 1.1 geração de US sem critérios ----------

def test_story_prompt_does_not_ask_for_acceptance_criteria(container, project_id, audio_id):
    generate(container, project_id, [audio_id])
    [prompt] = container.llm.prompts_for("GERACAO_US_QUS")
    assert "### TAREFA: GERACAO_US_QUS" in prompt
    response_format = prompt.split("## FORMATO DA RESPOSTA")[1]
    assert "criterios_aceitacao" not in response_format and "Dado" not in response_format
    assert "NÃO escreva critérios de aceitação" in prompt
    assert container.llm.schemas["GERACAO_US_QUS"] == prompts.STORY_SCHEMA


def test_criteria_returned_by_story_stage_are_discarded(container, project_id, audio_id):
    """O default do fake ainda devolve um critério na etapa de US (formato antigo): ele é descartado e contado;
    os critérios gravados vêm da etapa própria."""
    job = generate(container, project_id, [audio_id])
    result = job["resultado"]
    assert result["validacao"]["historias"]["criterios_descartados"] == 1
    first = detail(container, result["historias_criadas"][0])
    assert "Dado uma turma, quando emitir" not in json.dumps(first["criterios_aceitacao"], ensure_ascii=False)
    assert {c["origem"] for c in first["criterios_aceitacao"]} == {"ia"}


def test_each_source_records_what_it_supports_and_checks_its_own_citation(container, project_id, audio_id, document_id):
    def handler(prompt):
        f_audio, f_doc = labels_in(prompt)[0], labels_in(prompt)[-1]
        return one_story([
            {"trecho_id": f_audio, "afirmacoes_sustentadas": ["funcionalidade", "perfil"],
             "citacao": "Precisamos emitir o boletim da turma"},
            {"trecho_id": f_doc, "afirmacoes_sustentadas": ["beneficio"], "citacao": "frase que não está no trecho"},
            {"trecho_id": "F77", "afirmacoes_sustentadas": ["perfil"], "citacao": "x"},
        ])(prompt)
    container.llm.handlers["GERACAO_US_QUS"] = handler
    job = generate(container, project_id, [audio_id], [document_id])
    story = detail(container, job["resultado"]["historias_criadas"][0])
    with container.uow() as uow:
        evidences = uow.evidences.list_by_story(story["id"])
    claims = {e["tipo_fonte"]: e["afirmacao"] for e in evidences}
    assert claims["audio"] == "Sustenta: funcionalidade, perfil"
    assert claims["documento"] == "Sustenta: beneficio"
    unsupported = story["afirmacoes_sem_evidencia"]
    assert any(c.startswith("Citação não encontrada no trecho") and "frase que não está" in c for c in unsupported)
    assert "Referência a trecho inexistente nas fontes: F77" in unsupported
    assert not any("Precisamos emitir" in c for c in unsupported)  # a citação correta passa
    validation = job["resultado"]["validacao"]["historias"]
    assert validation["citacoes_divergentes"] == 1 and validation["referencias_invalidas"] == 1


def test_conflicts_and_coverage_from_story_stage(container, project_id, audio_id):
    def handler(prompt):
        return {"historias": [
            {"id": "H1", "funcionalidade_ref": "FN1", "perfil": "coordenador pedagógico",
             "funcionalidade": "emitir o boletim da turma", "beneficio": "acompanhar", "fontes": ["F1"]},
            {"id": "H2", "perfil": "coordenador pedagógico", "funcionalidade": "bloquear a emissão do boletim",
             "beneficio": "evitar erros", "fontes": ["F1"]},
        ], "conflitos_identificados": [{"historias": ["H1", "H2"], "trechos": ["F1"], "descricao": "Emitir × bloquear."}],
            "cobertura_funcional": [{"funcionalidade_ref": "FN1", "historias": ["H1"], "observacao": None},
                                    {"funcionalidade_ref": "FN9", "historias": []}]}
    container.llm.handlers["GERACAO_US_QUS"] = handler
    job = generate(container, project_id, [audio_id])
    first, second = (detail(container, i) for i in job["resultado"]["historias_criadas"])
    for story in (first, second):
        assert any(g["elemento"] == "conflito" and "Emitir × bloquear" in g["descricao"] for g in story["informacoes_ausentes"])
    assert [c["funcionalidade_ref"] for c in job["resultado"]["cobertura_funcional"]] == ["FN1"]  # FN9 não existe


# ---------- 1.2 critérios gerados à parte ----------

def test_criteria_stage_receives_stories_and_their_evidences_without_changing_them(container, project_id, audio_id):
    job = generate(container, project_id, [audio_id])
    [prompt] = container.llm.prompts_for("GERACAO_CRITERIOS_ACEITACAO")
    assert container.llm.schemas["GERACAO_CRITERIOS_ACEITACAO"] == prompts.CRITERIA_SCHEMA
    assert "[H1] Como coordenador pedagógico, quero emitir o boletim da turma" in prompt
    assert re.search(r"^Evidências vinculadas: F\d", prompt, flags=re.M)
    assert "NÃO altere a história" in prompt
    first = detail(container, job["resultado"]["historias_criadas"][0])
    assert first["historia"] == (
        "Como coordenador pedagógico, quero emitir o boletim da turma, para acompanhar o desempenho dos alunos."
    )
    criterion = first["criterios_aceitacao"][0]
    assert criterion["id"] == "CA01"
    assert criterion["criterio"] == (
        "Dado o usuário autorizado, quando ele executa a funcionalidade, então o sistema registra o resultado."
    )
    assert (criterion["dado"], criterion["quando"], criterion["entao"]) == (
        "o usuário autorizado", "ele executa a funcionalidade", "o sistema registra o resultado")
    with container.uow() as uow:
        [evidence] = uow.evidences.list_by_story(first["id"])
    assert criterion["fontes"] == [{"trecho_id": evidence["trecho_id"], "afirmacoes_sustentadas": ["entao"]}]
    assert criterion["afirmacoes_sem_evidencia"] == []
    # a versão 1 já nasce com os critérios (persistência só depois da etapa de critérios)
    assert first["versoes"][0]["conteudo"]["criterios_aceitacao"][0]["dado"] == "o usuário autorizado"


def test_criteria_structural_validation_flags_problems(container, project_id, audio_id):
    def handler(prompt):
        label = re.search(r"Evidências vinculadas: (F\d+)", prompt).group(1)
        return {"historias": [{"id": "H1", "criterios_aceitacao": [
            {"dado": "Dado que há uma turma", "quando": "quando o coordenador emitir", "entao": "então gera o PDF",
             "fontes": [{"trecho_id": label, "afirmacoes_sustentadas": ["entao"], "citacao": "texto inexistente"}]},
            {"dado": "há uma turma", "quando": "o coordenador emitir", "entao": "gera o PDF"},  # duplicado
            {"dado": "x", "quando": "y", "entao": "z", "fontes": ["F99"]},
        ]}]}
    container.llm.handlers["GERACAO_US_QUS"] = one_story(["F1"])
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = handler
    job = generate(container, project_id, [audio_id])
    story = detail(container, job["resultado"]["historias_criadas"][0])
    first, third = story["criterios_aceitacao"]
    assert first["criterio"] == "Dado há uma turma, quando o coordenador emitir, então gera o PDF."  # prefixos removidos
    assert any(c.startswith("Citação não encontrada no trecho") for c in first["afirmacoes_sem_evidencia"])
    assert "Referência a trecho inexistente nas fontes: F99" in third["afirmacoes_sem_evidencia"]
    assert CRITERION_WITHOUT_EVIDENCE in third["afirmacoes_sem_evidencia"]
    validation = job["resultado"]["validacao"]["criterios"]
    assert validation["criterios"] == 2 and validation["criterios_sem_evidencia"] == 1
    assert validation["citacoes_divergentes"] == 1 and validation["referencias_invalidas"] == 1


def test_criterion_citing_related_chunk_adds_it_as_story_evidence(container, project_id, audio_id, document_id):
    container.llm.handlers["GERACAO_US_QUS"] = lambda p: one_story([labels_in(p)[0]])(p)  # só o áudio

    def criteria(prompt):
        related = re.findall(r"^\[(F\d+)\] \(documento .*— relacionado\)", prompt, flags=re.M)
        assert related, "trechos relacionados entram no prompt"
        return {"historias": [{"id": "H1", "criterios_aceitacao": [
            {"dado": "o fim do bimestre", "quando": "o coordenador emitir o boletim", "entao": "o boletim sai em PDF",
             "fontes": [{"trecho_id": related[0], "afirmacoes_sustentadas": ["entao"]}]}]}]}
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = criteria
    job = generate(container, project_id, [audio_id], [document_id])
    story = detail(container, job["resultado"]["historias_criadas"][0])
    assert story["fontes_documentos"], "o trecho citado pelo critério virou evidência da história"
    with container.uow() as uow:
        doc = next(e for e in uow.evidences.list_by_story(story["id"]) if e["tipo_fonte"] == "documento")
    assert doc["afirmacao"] == "Sustenta critério de aceitação"
    assert story["criterios_aceitacao"][0]["fontes"][0]["trecho_id"] == doc["trecho_id"]


def test_no_basis_returns_explicit_gap_instead_of_invented_criteria(container, project_id, audio_id):
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = lambda p: {"historias": [
        {"id": h, "criterios_aceitacao": [], "lacunas_criterios": [{"elemento": "regras", "descricao": "Falta o formato."}]}
        for h in re.findall(r"^\[(H\d+)\]", p, flags=re.M)]}
    job = generate(container, project_id, [audio_id])
    story = detail(container, job["resultado"]["historias_criadas"][0])
    assert story["criterios_aceitacao"] == []
    assert {"elemento": "criterios_aceitacao", "descricao": "Falta o formato."} in story["informacoes_ausentes"]
    assert story["status"] == "em_revisao"  # lacuna de critérios não é lacuna do template da história


# ---------- 1.3 orquestração ----------

def test_pipeline_records_stages_durations_and_prompt_versions(container, project_id, audio_id):
    result = generate(container, project_id, [audio_id])["resultado"]
    assert [s["etapa"] for s in result["etapas"]] == STAGES
    assert all(s["status"] == "concluida" and s["duracao_s"] >= 0 for s in result["etapas"])
    assert result["versoes_prompt"]["GERACAO_US_QUS"] == "1.0"
    assert result["versoes_prompt"]["GERACAO_CRITERIOS_ACEITACAO"] == "1.0"
    criteria_stage = next(s for s in result["etapas"] if s["etapa"] == "geracao_criterios")
    assert criteria_stage["historias"] == 2 and criteria_stage["falhas"] == 0
    assert len(container.llm.prompts_for("GERACAO_CRITERIOS_ACEITACAO")) == 1  # as 2 histórias numa chamada


def test_criteria_failure_keeps_stories_and_flags_them(container, project_id, audio_id):
    def failing(prompt):
        raise ExternalServiceError("LLM indisponível (simulado).")
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = failing
    job = generate(container, project_id, [audio_id])
    assert job["status"] == "concluido" and job["resultado"]["total"] == 2
    stage = next(s for s in job["resultado"]["etapas"] if s["etapa"] == "geracao_criterios")
    assert stage["status"] == "erro" and "todas as histórias" in stage["erro"]
    assert job["resultado"]["criterios"]["falhas"]
    story = detail(container, job["resultado"]["historias_criadas"][0])
    assert story["criterios_aceitacao"] == []
    assert {"elemento": "criterios_aceitacao", "descricao": FAILED} in story["informacoes_ausentes"]


def test_story_missing_from_criteria_answer_is_flagged(container, project_id, audio_id):
    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = lambda p: {"historias": default_criteria(p)["historias"][:1]}
    job = generate(container, project_id, [audio_id])
    second = detail(container, job["resultado"]["historias_criadas"][1])
    assert any("não devolveu critérios" in g["descricao"] for g in second["informacoes_ausentes"])


def test_criteria_stage_can_be_disabled(container, project_id, audio_id):
    container.settings.generation_criteria = False
    job = generate(container, project_id, [audio_id])
    stage = next(s for s in job["resultado"]["etapas"] if s["etapa"] == "geracao_criterios")
    assert stage["status"] == "ignorada"
    assert not container.llm.prompts_for("GERACAO_CRITERIOS_ACEITACAO")
    story = detail(container, job["resultado"]["historias_criadas"][0])
    assert story["criterios_aceitacao"] == []
    assert {"elemento": "criterios_aceitacao", "descricao": NOT_GENERATED} in story["informacoes_ausentes"]


def test_story_stage_failure_records_stages_in_failed_job(container, project_id, audio_id):
    container.llm.fail = True
    job = generate(container, project_id, [audio_id])
    assert job["status"] == "erro"
    stages = {s["etapa"]: s["status"] for s in job["resultado"]["etapas"]}
    assert stages == {"preparacao": "concluida", "geracao_us": "erro"}


# ---------- execução independente ----------

@pytest.fixture
def stored_story(container, project_id, audio_id):
    container.settings.generation_criteria = False
    story_id = generate(container, project_id, [audio_id])["resultado"]["historias_criadas"][0]
    container.settings.generation_criteria = True
    container.llm.calls.clear()
    return story_id


def test_generate_criteria_for_stored_story_creates_version(container, stored_story):
    with container.uow() as uow:
        result = CriteriaService(uow, container).generate(stored_story, "ana")
    assert len(result["criterios_aceitacao"]) == 2 and result["versao_atual"] == 2
    assert not any(g["elemento"] == "criterios_aceitacao" for g in result["informacoes_ausentes"])
    v1, v2 = result["versoes"]
    assert v1["conteudo"]["criterios_aceitacao"] == []  # versão anterior preservada
    assert v2["origem_alteracao"] == "geracao_criterios_ia" and v2["originada_por_ia"] is True
    assert v2["responsavel"] == "ana"


def test_generate_criteria_refuses_approved_story(container, stored_story):
    with container.uow() as uow:
        StoryService(uow).set_status(stored_story, StoryStatus.APPROVED)
    with container.uow() as uow, pytest.raises(ConflictError) as exc:
        CriteriaService(uow, container).generate(stored_story, "ana")
    assert exc.value.details["motivo"] == "historia_aprovada"
    assert not container.llm.prompts_for("GERACAO_CRITERIOS_ACEITACAO")


def test_generate_criteria_llm_failure_changes_nothing(container, stored_story):
    container.llm.fail = True
    with container.uow() as uow, pytest.raises(ExternalServiceError):
        CriteriaService(uow, container).generate(stored_story, "ana")
    story = detail(container, stored_story)
    assert story["versao_atual"] == 1 and story["criterios_aceitacao"] == []


def test_batch_job_skips_approved_and_existing_unless_replace(container, project_id, audio_id):
    created = generate(container, project_id, [audio_id])["resultado"]["historias_criadas"]
    with container.uow() as uow:
        StoryService(uow).set_status(created[1], StoryStatus.APPROVED)
    container.llm.calls.clear()

    def run(replace):
        with container.uow() as uow:
            job = start_criteria_generation(uow, project_id, [], replace, "ana")
        run_criteria_generation(container, job["id"])
        with container.uow() as uow:
            return uow.jobs.get(job["id"])

    job = run(False)
    assert job["status"] == "concluido" and job["resultado"]["total"] == 0
    reasons = {i["historia_id"]: i["motivo"] for i in job["resultado"]["ignoradas"]}
    assert reasons == {created[0]: "ja_possui_criterios", created[1]: "historia_aprovada"}
    assert not container.llm.prompts_for("GERACAO_CRITERIOS_ACEITACAO")

    container.llm.handlers["GERACAO_CRITERIOS_ACEITACAO"] = lambda p: {"historias": [{"id": h, "criterios_aceitacao": [
        {"dado": "um bimestre encerrado", "quando": "o coordenador emitir", "entao": "o boletim fica disponível",
         "fontes": [{"trecho_id": "F1", "afirmacoes_sustentadas": ["entao"]}]}]}
        for h in re.findall(r"^\[(H\d+)\]", p, flags=re.M)]}
    job = run(True)
    assert job["resultado"]["historias_atualizadas"] == [created[0]]
    story = detail(container, created[0])
    assert story["criterios_aceitacao"][0]["entao"] == "o boletim fica disponível"
    assert story["versao_atual"] == 2 and story["versoes"][0]["conteudo"]["criterios_aceitacao"]


def test_criteria_api_routes(container, stored_story, project_id):
    with TestClient(create_app(container=container), raise_server_exceptions=False) as client:
        response = client.post(f"/api/v1/historias/{stored_story}/criterios/gerar", headers={"X-Usuario": "ana"})
        assert response.status_code == 200 and len(response.json()["criterios_aceitacao"]) == 2
        criterion = response.json()["criterios_aceitacao"][0]
        assert {"dado", "quando", "entao", "fontes", "origem"} <= set(criterion)
        job = client.post(f"/api/v1/projetos/{project_id}/criterios/gerar", json={"substituir": True})
        assert job.status_code == 202 and job.json()["tipo"] == "geracao_criterios"
        assert client.get(f"/api/v1/jobs/{job.json()['id']}").json()["status"] == "concluido"
        client.post(f"/api/v1/historias/{stored_story}/aprovar")
        conflict = client.post(f"/api/v1/historias/{stored_story}/criterios/gerar")
        assert conflict.status_code == 409 and conflict.json()["erro"]["detalhes"]["motivo"] == "historia_aprovada"
        assert client.post("/api/v1/historias/999/criterios/gerar").status_code == 404


def test_manual_edit_of_criteria_list_keeps_traceability_of_unchanged_criteria(container, stored_story):
    """O frontend reenvia só título e texto ao adicionar/remover um critério: os critérios que não mudaram
    mantêm Dado/Quando/Então e fontes; o alterado perde as fontes (elas sustentavam o texto anterior)."""
    with container.uow() as uow:
        generated = CriteriaService(uow, container).generate(stored_story, "ana")["criterios_aceitacao"]
    payload = [{"titulo": generated[0]["titulo"], "criterio": generated[0]["criterio"]},
               {"titulo": "Editado", "criterio": "Dado algo novo, quando muda, então outro resultado."},
               {"criterio": "Dado um critério manual, quando adicionado, então aparece."}]
    with container.uow() as uow:
        kept, edited, added = StoryService(uow).update(stored_story, {"criterios_aceitacao": payload}, "ana")["criterios_aceitacao"]
    assert kept["fontes"] == generated[0]["fontes"] and kept["dado"] == generated[0]["dado"]
    assert kept["origem"] == "ia"
    assert "fontes" not in edited and "origem" not in edited
    assert "fontes" not in added
