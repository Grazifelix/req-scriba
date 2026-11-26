"""US-05, US-06, US-16, US-17, US-19, US-20: geração, revisão, evidências e versões."""

import pytest

from app.core.errors import BusinessValidationError, ExternalServiceError, NotFoundError
from app.domain.enums import StoryStatus
from app.services.generation import run_generation, start_generation
from app.services.projects import ProjectService
from app.services.stories import StoryService
from tests.conftest import ensure_approved_analysis


def generate(container, project_id, audio_ids=(), document_ids=()):
    ensure_approved_analysis(container, project_id)
    with container.uow() as uow:
        job = start_generation(uow, project_id, list(audio_ids), list(document_ids), "ana")
    assert job["status"] == "pendente"
    run_generation(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def detail(container, story_id):
    with container.uow() as uow:
        return StoryService(uow).detail(story_id)


def test_generation_creates_standardized_candidates_for_review(container, project_id, audio_id):
    job = generate(container, project_id, [audio_id])
    assert job["status"] == "concluido" and job["resultado"]["total"] == 2  # US-05 CA01
    first, second = (detail(container, i) for i in job["resultado"]["historias_criadas"])
    assert first["historia"] == (
        "Como coordenador pedagógico, quero emitir o boletim da turma, para acompanhar o desempenho dos alunos."
    )  # US-05 CA02
    assert first["status"] == "em_revisao" and first["validada"] is False  # US-05 CA04 / US-06 CA04
    assert first["codigo"] == "US-001" and first["criterios_aceitacao"][0]["id"] == "CA01"
    # US-05 CA03: ausência indicada sem suposição
    assert second["status"] == "falta_informacao"
    assert "[A DEFINIR: perfil]" in second["historia"] and "[A DEFINIR: benefício]" in second["historia"]
    # sem evidência válida (F2/F99 não existem), a etapa de critérios registra a lacuna em vez de inventar
    assert {e["elemento"] for e in second["informacoes_ausentes"]} == {"perfil", "beneficio", "criterios_aceitacao"}


def test_generation_links_evidence_with_time_reference(container, project_id, audio_id):
    job = generate(container, project_id, [audio_id])
    story = detail(container, job["resultado"]["historias_criadas"][0])
    assert story["possui_suporte_documental"]  # US-16 CA01
    [evidence] = story["fontes_audio"]
    assert evidence["nome_arquivo"] == "reuniao.wav" and evidence["audio_id"] == audio_id  # US-16 CA04
    assert evidence["inicio_segundos"] == 0.0 and evidence["url_momento"].endswith("#t=0.0")  # US-17 CA02
    assert "boletim" in evidence["trecho"]  # texto vem do banco, não do LLM


def test_invalid_label_creates_no_evidence_and_is_flagged(container, project_id, document_id):
    def only_invalid(prompt):
        return {"historias": [{"funcionalidade": "gerar relatórios", "perfil": "diretor", "beneficio": "decidir",
                               "fontes": ["F42"]}]}
    container.llm.handlers["GERACAO_US_QUS"] = only_invalid
    job = generate(container, project_id, document_ids=[document_id])
    story = detail(container, job["resultado"]["historias_criadas"][0])
    assert not story["possui_suporte_documental"]  # US-16 CA03
    assert any("Nenhum trecho" in c for c in story["afirmacoes_sem_evidencia"])


def test_multiple_sources_and_document_location(container, project_id, audio_id, document_id):
    def cite_all(prompt):
        import re

        labels = re.findall(r"^\[(F\d+)\]", prompt, flags=re.M)
        return {"historias": [{"funcionalidade": "emitir o boletim em PDF", "perfil": "coordenador",
                               "beneficio": "acompanhar notas", "fontes": labels}]}
    container.llm.handlers["GERACAO_US_QUS"] = cite_all
    job = generate(container, project_id, [audio_id], [document_id])
    story = detail(container, job["resultado"]["historias_criadas"][0])
    assert story["fontes_audio"] and story["fontes_documentos"]  # US-16 CA02
    doc_evidence = story["fontes_documentos"][0]
    assert doc_evidence["documento_id"] == document_id and doc_evidence["inicio_caractere"] is not None  # US-17 CA03
    rag_prompt = container.llm.prompts_for("GERACAO_US_QUS")[0]
    assert "documento escopo.txt" in rag_prompt  # documentos recuperados por RAG junto à transcrição


def test_rerun_does_not_duplicate_existing_stories(container, project_id, audio_id):
    generate(container, project_id, [audio_id])
    job = generate(container, project_id, [audio_id])
    assert job["resultado"]["total"] == 0
    assert job["resultado"]["ignoradas_por_duplicidade"] == ["US-001", "US-002"]
    assert job["resultado"]["mensagem"]


def test_generation_dedupes_across_batches(container, project_id, audio_id):
    container.settings.generation_batch_chars = 60  # força um lote por janela
    container.settings.transcript_window_chars = 60
    from app.services.indexing import index_audio

    with container.uow() as uow:
        index_audio(uow, container, uow.audios.get(audio_id))
    job = generate(container, project_id, [audio_id])
    assert job["resultado"]["lotes"] == 3 and job["resultado"]["total"] == 2


def test_llm_failure_marks_job_error(container, project_id, audio_id):
    container.llm.fail = True
    job = generate(container, project_id, [audio_id])
    assert job["status"] == "erro" and "Todos os lotes falharam" in job["mensagem_erro"]


def test_generation_requires_valid_sources(container, project_id):
    with container.uow() as uow, pytest.raises(BusinessValidationError, match="ao menos uma fonte"):
        start_generation(uow, project_id, [], [], "ana")
    with container.uow() as uow, pytest.raises(BusinessValidationError):
        start_generation(uow, project_id, [12345], [], "ana")


def test_review_edit_approve_reject_and_backlog(container, project_id, audio_id):
    job = generate(container, project_id, [audio_id])
    first_id, second_id = job["resultado"]["historias_criadas"]
    with container.uow() as uow:
        service = StoryService(uow)
        edited = service.update(second_id, {"perfil": "secretária", "beneficio": "manter o cadastro"}, "ana")
    assert edited["status"] == "em_revisao"  # US-06 CA01
    assert [e["elemento"] for e in edited["informacoes_ausentes"]] == ["criterios_aceitacao"]
    assert edited["versao_atual"] == 2
    with container.uow() as uow:
        approved = StoryService(uow).set_status(first_id, StoryStatus.APPROVED)
        rejected = StoryService(uow).set_status(second_id, StoryStatus.REJECTED)
        project = ProjectService(uow, container).get(project_id)
    assert approved["status"] == "aprovada" and approved["validada"]  # US-06 CA02
    assert approved["versao_atual"] == 1  # aprovação não cria versão
    assert rejected["status_rotulo"] == "Desaprovada"
    assert project["backlog_aprovado"] == [first_id] and second_id not in project["backlog"]  # US-06 CA03
    assert project["total_historias_prontas"] == 1 and project["total_historias_desaprovadas"] == 1


def test_update_with_story_text_and_no_change_keeps_version(container, project_id):
    with container.uow() as uow:
        story = StoryService(uow).create_manual(project_id, {"funcionalidade": "lançar notas"}, "ana")
    assert story["origem"] == "manual" and story["status"] == "falta_informacao"
    with container.uow() as uow:
        service = StoryService(uow)
        updated = service.update(story["id"], {"historia": "Como professor, quero lançar notas, para informar os alunos."}, "ana")
        unchanged = service.update(story["id"], {"perfil": "professor"}, "ana")
        with pytest.raises(BusinessValidationError, match="padrão"):
            service.update(story["id"], {"historia": "texto livre"}, "ana")
    assert updated["perfil"] == "professor" and updated["versao_atual"] == 2
    assert unchanged["versao_atual"] == 2  # nada mudou → nenhuma versão nova


def test_apply_rewrite_splits_story_and_copies_evidence(container, project_id, audio_id):
    job = generate(container, project_id, [audio_id])
    story_id = job["resultado"]["historias_criadas"][0]
    rewrite = ("H1: Como coordenador pedagógico, quero emitir o boletim da turma, para acompanhar o desempenho; "
               "H2: Como coordenador pedagógico, quero exportar o boletim em PDF, para enviar aos pais")
    with container.uow() as uow:
        result = StoryService(uow, container).apply_rewrite(story_id, rewrite, "ana")
    original, [created] = result["historia"], result["historias_criadas"]
    # critérios próprios de cada parte, escritos pelo LLM com as evidências da original
    assert [c["id"] for c in created["criterios_aceitacao"]] == ["CA01", "CA02", "CA03"]
    assert "exportar o boletim em PDF" in created["criterios_aceitacao"][0]["criterio"]
    assert "emitir o boletim da turma" in original["criterios_aceitacao"][0]["criterio"]
    [prompt] = container.llm.prompts_for("CRITERIOS_DIVISAO")
    assert "boletim" in prompt.split("EVIDÊNCIAS:")[1]
    assert original["versao_atual"] == 2 and original["funcionalidade"] == "emitir o boletim da turma"
    assert original["beneficio"] == "acompanhar o desempenho"
    assert created["funcionalidade"] == "exportar o boletim em PDF" and created["versao_atual"] == 1
    assert created["origem"] == "manual" and created["status"] == "em_revisao"
    assert created["codigo"] == "US-003"
    assert [e["trecho"] for e in created["fontes_audio"]] == [e["trecho"] for e in original["fontes_audio"]]
    assert created["fontes_audio"][0]["origem"] == original["fontes_audio"][0]["origem"]


def test_apply_rewrite_without_criteria_keeps_original_and_flags_new_story(container, project_id):
    container.llm.handlers["CRITERIOS_DIVISAO"] = lambda prompt: {"historias": []}
    with container.uow() as uow:
        story = StoryService(uow).create_manual(project_id, {
            "funcionalidade": "lançar notas", "criterios_aceitacao": [{"criterio": "Dado x, quando y, então z."}],
        }, "ana")
    rewrite = "H1: Como professor, quero lançar notas, para avaliar; H2: Como professor, quero corrigir notas, para ajustar"
    with container.uow() as uow:
        result = StoryService(uow, container).apply_rewrite(story["id"], rewrite, "ana")
    [created] = result["historias_criadas"]
    assert result["historia"]["criterios_aceitacao"][0]["criterio"] == "Dado x, quando y, então z."
    assert created["criterios_aceitacao"] == []
    assert "criterios_aceitacao" in {e["elemento"] for e in created["informacoes_ausentes"]}


def test_apply_rewrite_llm_failure_saves_nothing(container, project_id):
    with container.uow() as uow:
        story = StoryService(uow).create_manual(project_id, {"funcionalidade": "lançar notas"}, "ana")
    container.llm.fail = True
    rewrite = "H1: Como professor, quero lançar notas, para avaliar; H2: Como professor, quero corrigir notas, para ajustar"
    with pytest.raises(ExternalServiceError):
        with container.uow() as uow:
            StoryService(uow, container).apply_rewrite(story["id"], rewrite, "ana")
    with container.uow() as uow:
        assert len(uow.stories.list_by_project(project_id)) == 1
        assert StoryService(uow).detail(story["id"])["versao_atual"] == 1


def test_apply_rewrite_with_single_story_and_invalid_part(container, project_id):
    with container.uow() as uow:
        story = StoryService(uow).create_manual(project_id, {"funcionalidade": "lançar notas"}, "ana")
    with container.uow() as uow:
        result = StoryService(uow).apply_rewrite(
            story["id"], "Como professor, quero lançar notas, para informar os alunos.", "ana"
        )
    assert result["historias_criadas"] == [] and result["historia"]["versao_atual"] == 2
    with pytest.raises(BusinessValidationError, match="padrão"):
        with container.uow() as uow:
            StoryService(uow, container).apply_rewrite(story["id"], "H1: Como a, quero b, para c; H2: texto livre", "ana")
    with container.uow() as uow:
        assert len(uow.stories.list_by_project(project_id)) == 1  # nada criado nem alterado
        assert StoryService(uow).detail(story["id"])["versao_atual"] == 2


def test_versions_record_author_date_and_ai_origin(container, project_id, audio_id):
    job = generate(container, project_id, [audio_id])
    story_id = job["resultado"]["historias_criadas"][0]
    with container.uow() as uow:
        single = StoryService(uow).versions(story_id)
    assert single["total"] == 1  # US-20 CA03: registro de criação
    creation = single["itens"][0]
    assert creation["origem_alteracao"] == "geracao_ia" and creation["originada_por_ia"]  # US-19 CA01/CA04
    assert creation["tipo_alteracao"] == "automatica" and creation["responsavel"] == "sistema"
    with container.uow() as uow:
        StoryService(uow).update(story_id, {"titulo": "Boletim bimestral"}, "carla")
        versions = StoryService(uow).versions(story_id)["itens"]
        old = StoryService(uow).version(story_id, 1)
    assert [v["numero"] for v in versions] == [1, 2]  # US-20 CA01
    assert versions[1]["responsavel"] == "carla" and versions[1]["tipo_alteracao"] == "manual"  # US-19 CA02/CA03
    assert versions[1]["data_hora"]
    assert old["conteudo"]["titulo"] == "Emitir boletim"  # US-20 CA02
    with container.uow() as uow, pytest.raises(NotFoundError):
        StoryService(uow).version(story_id, 9)


def test_traceability_navigation(container, project_id, audio_id, document_id):
    def cite_two(prompt):
        return {"historias": [{"funcionalidade": "emitir boletim", "perfil": "coordenador", "beneficio": "acompanhar",
                               "fontes": ["F1", "F2"]}]}
    container.llm.handlers["GERACAO_US_QUS"] = cite_two
    job = generate(container, project_id, [audio_id], [document_id])
    story_id = job["resultado"]["historias_criadas"][0]
    with container.uow() as uow:
        trace = StoryService(uow).traceability(story_id)
    nav = trace["navegacao"]
    assert trace["total_evidencias"] == 2 and nav[0]["anterior"] is None  # US-17 CA04
    assert nav[0]["proxima"] == nav[1]["evidencia_id"] and nav[1]["proxima"] is None
    assert trace["versoes"][0]["novas_evidencia_ids"] == [n["evidencia_id"] for n in nav]
