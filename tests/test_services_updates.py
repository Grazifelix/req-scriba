"""US-13, US-14, US-15, US-18: novas fontes, sugestões e rastreabilidade por versão."""

import pytest

from app.core.errors import ConflictError
from app.domain.enums import StoryStatus
from app.services.generation import run_generation, start_generation
from app.services.stories import StoryService
from app.services.updates import UpdateService, run_update_analysis, start_analysis
from tests.conftest import upload_audio, upload_document

NEW_DOC = (
    "Ata da segunda entrevista: o boletim será disponibilizado em PDF para download.\n\n"
    "Os responsáveis querem receber notificações por e-mail quando houver faltas."
)


@pytest.fixture
def approved_story(container, project_id, audio_id):
    with container.uow() as uow:
        job = start_generation(uow, project_id, [audio_id], [], "ana")
    run_generation(container, job["id"])
    with container.uow() as uow:
        story_id = uow.jobs.get(job["id"])["resultado"]["historias_criadas"][0]
        StoryService(uow).set_status(story_id, StoryStatus.APPROVED)
    return story_id


def analyze(container, project_id, document_ids):
    with container.uow() as uow:
        job = start_analysis(uow, project_id, [], document_ids, "ana")
    run_update_analysis(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def suggestions(container, project_id):
    with container.uow() as uow:
        return UpdateService(uow).list(project_id)["itens"]


def story(container, story_id):
    with container.uow() as uow:
        return StoryService(uow).detail(story_id)


def test_analysis_suggests_without_touching_approved_story(container, project_id, approved_story):
    before = story(container, approved_story)
    new_doc = upload_document(container, project_id, NEW_DOC, "ata2.txt")  # US-13 CA01
    job = analyze(container, project_id, [new_doc])
    assert job["status"] == "concluido" and job["resultado"]["historias_analisadas"] == 2  # US-13 CA02
    items = suggestions(container, project_id)
    update = next(s for s in items if s["historia_id"] == approved_story)
    assert update["tipo"] == "complementa" and update["status"] == "pendente"  # US-13 CA03 / US-14 CA01
    after = story(container, approved_story)
    assert after["historia"] == before["historia"] and after["versao_atual"] == 1  # US-14 CA04
    assert after["atualizacoes_pendentes"] == 1


def test_suggestion_shows_comparison(container, project_id, approved_story):
    new_doc = upload_document(container, project_id, NEW_DOC, "ata2.txt")
    analyze(container, project_id, [new_doc])
    suggestion_id = next(s["id"] for s in suggestions(container, project_id) if s["historia_id"] == approved_story)
    with container.uow() as uow:
        detail = UpdateService(uow).get(suggestion_id)
    comparison = detail["comparacao"]  # US-14 CA02
    assert "funcionalidade" in comparison["campos_alterados"] and "historia" in comparison["campos_alterados"]
    assert "+PDF" in comparison["diff_texto"]
    assert detail["evidencias"][0]["nome_arquivo"] == "ata2.txt"


def test_unmatched_information_becomes_new_requirement(container, project_id, approved_story):
    def extraction(prompt):
        return {"historias": [{"perfil": "responsável", "funcionalidade": "receber notificações de faltas por e-mail",
                               "beneficio": "acompanhar a frequência", "fontes": ["F1"]}]}
    container.llm.handlers["EXTRACAO_HISTORIAS"] = extraction
    container.settings.chunk_size, container.settings.chunk_overlap = 90, 10
    new_doc = upload_document(container, project_id, NEW_DOC, "ata2.txt")
    analyze(container, project_id, [new_doc])
    new_req = [s for s in suggestions(container, project_id) if s["tipo"] == "novo_requisito"]
    assert len(new_req) == 1 and new_req[0]["historia_id"] is None  # US-14 CA03
    with container.uow() as uow:
        applied = UpdateService(uow).approve(new_req[0]["id"], "ana")
    created = story(container, applied["historia_criada_id"])
    assert created["status"] == "em_revisao" and created["fontes_documentos"][0]["origem"] == "atualizacao"
    assert created["versoes"][0]["origem_alteracao"] == "sugestao_ia"


def test_approve_applies_and_versions_with_new_evidence(container, project_id, approved_story):
    original_evidence = story(container, approved_story)["fontes_audio"][0]["evidencia_id"]
    new_doc = upload_document(container, project_id, NEW_DOC, "ata2.txt")
    analyze(container, project_id, [new_doc])
    suggestion_id = next(s["id"] for s in suggestions(container, project_id) if s["historia_id"] == approved_story)
    with container.uow() as uow:
        result = UpdateService(uow).approve(suggestion_id, "bruno")
    assert result["status"] == "aprovada" and result["versao_aplicada"] == 2  # US-15 CA02
    updated = story(container, approved_story)
    assert updated["funcionalidade"] == "emitir o boletim da turma em PDF" and updated["status"] == "aprovada"
    v2 = updated["versoes"][1]
    assert v2["origem_alteracao"] == "sugestao_ia" and v2["tipo_alteracao"] == "automatica"  # US-15 CA04 / US-19 CA04
    assert v2["responsavel"] == "bruno" and v2["sugestao_atualizacao_id"] == suggestion_id
    with container.uow() as uow:
        trace = StoryService(uow).traceability(approved_story)
        v1_evidence = StoryService(uow).evidences(approved_story, 1)
    assert [e["evidencia_id"] for e in v1_evidence["itens"]] == [original_evidence]  # US-18 CA02
    assert len(trace["versoes"][1]["evidencias"]) == 2  # US-18 CA03
    new_ids = trace["versoes"][1]["novas_evidencia_ids"]
    assert len(new_ids) == 1 and new_ids[0] != original_evidence  # US-18 CA01
    with container.uow() as uow, pytest.raises(ConflictError):
        UpdateService(uow).approve(suggestion_id, "bruno")


def test_edit_then_approve_marks_edited_origin(container, project_id, approved_story):
    new_doc = upload_document(container, project_id, NEW_DOC, "ata2.txt")
    analyze(container, project_id, [new_doc])
    suggestion_id = next(s["id"] for s in suggestions(container, project_id) if s["historia_id"] == approved_story)
    with container.uow() as uow:
        UpdateService(uow).approve(suggestion_id, "ana", {"beneficio": "acompanhar o desempenho bimestral"})
    updated = story(container, approved_story)
    assert updated["beneficio"] == "acompanhar o desempenho bimestral"  # US-15 CA01 (editar)
    assert updated["versoes"][-1]["origem_alteracao"] == "sugestao_ia_editada"


def test_reject_preserves_story(container, project_id, approved_story):
    before = story(container, approved_story)
    new_doc = upload_document(container, project_id, NEW_DOC, "ata2.txt")
    analyze(container, project_id, [new_doc])
    suggestion_id = next(s["id"] for s in suggestions(container, project_id) if s["historia_id"] == approved_story)
    with container.uow() as uow:
        rejected = UpdateService(uow).reject(suggestion_id, "ana")
    after = story(container, approved_story)
    assert rejected["status"] == "rejeitada" and rejected["decidido_por"] == "ana"  # US-15 CA03
    assert after["historia"] == before["historia"] and after["versao_atual"] == before["versao_atual"]


def test_new_audio_source_and_failure_handling(container, project_id, approved_story):
    new_audio = upload_audio(container, project_id, "entrevista2.wav")
    container.llm.fail = True
    with container.uow() as uow:
        job = start_analysis(uow, project_id, [new_audio], [], "ana")
    run_update_analysis(container, job["id"])
    with container.uow() as uow:
        result = uow.jobs.get(job["id"])
    assert result["status"] == "erro"  # extração de novos requisitos falhou → job em erro
