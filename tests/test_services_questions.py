"""US-10, US-11, US-12: perguntas de follow-up."""

import pytest

from app.core.errors import BusinessValidationError, ConflictError
from app.domain.enums import QuestionStatus
from app.services.quality import QualityService
from app.services.questions import QuestionService
from app.services.stories import StoryService


@pytest.fixture
def evaluated_story(container, project_id):
    with container.uow() as uow:
        story_id = StoryService(uow).create_manual(
            project_id, {"perfil": "coordenador", "funcionalidade": "emitir o boletim", "beneficio": "acompanhar"}, "ana"
        )["id"]
    with container.uow() as uow:
        QualityService(uow, container).evaluate(story_id)
    return story_id


def service(container, uow):
    return QuestionService(uow, container)


def test_generate_questions_linked_to_story_and_defect(container, evaluated_story):
    with container.uow() as uow:
        result = service(container, uow).generate(evaluated_story)
    assert result["total"] == 1  # US-10 CA01
    question = result["perguntas"][0]
    assert question["historia_codigo"] == "US-001"  # US-10 CA02
    assert question["defeito_chave"] == "qus:unambiguous" and question["tipo_defeito"] == "unambiguous"
    assert question["status"] == "pendente" and question["justificativa"]
    # US-10 CA04: o defeito sem pergunta aparece como limitação
    assert result["limitacoes"] and result["limitacoes"][0]["defeito_chave"].startswith("lacuna:")
    prompt = container.llm.prompts_for("PERGUNTAS_FOLLOWUP")[0]
    assert "(unambiguous)" in prompt and "(lacuna)" in prompt  # US-10 CA03: contexto por tipo de defeito


def test_generate_for_selected_defects_only(container, evaluated_story):
    with container.uow() as uow:
        result = service(container, uow).generate(evaluated_story, ["qus:unambiguous"])
        with pytest.raises(BusinessValidationError):
            service(container, uow).generate(evaluated_story, ["qus:inexistente"])
    assert result["total"] == 1 and result["limitacoes"] == []


def test_no_defects_reports_limitation(container, project_id):
    with container.uow() as uow:
        story_id = StoryService(uow).create_manual(
            project_id, {"perfil": "a", "funcionalidade": "b", "beneficio": "c"}, "ana")["id"]
        result = service(container, uow).generate(story_id)
    assert result["perguntas"] == [] and "Nenhum defeito" in result["limitacoes"][0]["motivo"]


def test_review_cycle_and_followup_grouping(container, project_id, evaluated_story):
    with container.uow() as uow:
        generated = service(container, uow).generate(evaluated_story)["perguntas"][0]
        extra = service(container, uow).generate(evaluated_story)["perguntas"][0]
    with container.uow() as uow:
        s = service(container, uow)
        edited = s.edit(generated["id"], "O boletim deve ser gerado em PDF ou planilha?")
        approved = s.set_status(generated["id"], QuestionStatus.APPROVED)
        rejected = s.set_status(extra["id"], QuestionStatus.REJECTED)
    assert edited["editada"] and edited["pergunta"].startswith("O boletim")  # US-11 CA01
    assert approved["disponivel_proxima_rodada"]  # US-11 CA03
    assert rejected["status"] == "rejeitada"
    with container.uow() as uow:
        by_story = service(container, uow).followup(project_id, "historia")
        by_type = service(container, uow).followup(project_id, "tipo_defeito")
    assert by_story["total_perguntas"] == 1  # US-11 CA02: rejeitada fora
    assert by_story["grupos"][0]["codigo"] == "US-001"  # US-12 CA01
    assert by_type["grupos"][0]["tipo_defeito"] == "unambiguous" and by_type["grupos"][0]["rotulo"]  # US-12 CA02
    assert by_story["pendentes_de_resposta"] == 1


def test_answer_links_to_question_and_requirement(container, project_id, evaluated_story, audio_id):
    with container.uow() as uow:
        question = service(container, uow).generate(evaluated_story)["perguntas"][0]
    with container.uow() as uow:
        s = service(container, uow)
        with pytest.raises(ConflictError):
            s.answer(question["id"], "PDF", None, None)  # ainda não aprovada
        s.set_status(question["id"], QuestionStatus.APPROVED)
        answered = s.answer(question["id"], "Em PDF.", "audio", audio_id)
    assert answered["status"] == "respondida" and answered["resposta"] == "Em PDF."  # US-12 CA03
    assert answered["historia_id"] == evaluated_story and answered["resposta_fonte_id"] == audio_id
    with container.uow() as uow, pytest.raises(ConflictError):
        service(container, uow).edit(question["id"], "nova")


def test_answer_source_must_belong_to_project(container, evaluated_story):
    with container.uow() as uow:
        question = service(container, uow).generate(evaluated_story)["perguntas"][0]
        service(container, uow).set_status(question["id"], QuestionStatus.APPROVED)
        with pytest.raises(BusinessValidationError):
            service(container, uow).answer(question["id"], "x", "audio", 999)
        with pytest.raises(BusinessValidationError):
            service(container, uow).answer(question["id"], "x", "audio", None)
