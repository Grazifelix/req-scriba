"""US-07, US-08, US-09: avaliação QUS, reavaliação, comparação e lacunas."""

import pytest

from app.core.errors import ConflictError, ExternalServiceError
from app.domain import qus
from app.services.quality import QualityService
from app.services.stories import StoryService


def create(container, project_id, **data):
    with container.uow() as uow:
        return StoryService(uow).create_manual(project_id, data, "ana")["id"]


def evaluate(container, story_id):
    with container.uow() as uow:
        return QualityService(uow, container).evaluate(story_id)


FULL = {"perfil": "coordenador pedagógico", "funcionalidade": "emitir o boletim da turma",
        "beneficio": "acompanhar o desempenho dos alunos"}


def test_evaluation_returns_13_criteria_with_justification(container, project_id):
    story_id = create(container, project_id, **FULL)
    result = evaluate(container, story_id)
    assert set(result["avaliacao_criterios"]) == set(qus.INDIVIDUAL_CRITERIA)  # US-07 CA01
    assert set(result["analise_transversal_conjunto"]) == set(qus.SET_CRITERIA)
    violated = result["avaliacao_criterios"]["unambiguous"]
    assert violated["resultado"] == "Atenção" and violated["evidencia"] and violated["acao"]  # US-07 CA02
    # 7 individuais + 5 de conjunto (história única no backlog) = 12 → 92,31 Alta
    assert result["qualidade_geral"] == {"porcentagem": 92.31, "status": "Alta", "criterios_atendidos": 12,
                                         "total_criterios": 13}


def test_alone_story_skips_llm_for_conflict_and_independence(container, project_id):
    story_id = create(container, project_id, **FULL)
    evaluate(container, story_id)
    [set_prompt] = container.llm.prompts_for("AVALIACAO_QUS_CONJUNTO")
    assert "- complete:" in set_prompt and "- conflict_free:" not in set_prompt


def test_deterministic_rules_override_llm(container, project_id):
    first = create(container, project_id, **FULL)
    duplicate = create(container, project_id, **FULL)
    incomplete = create(container, project_id, funcionalidade="cadastrar novos alunos")
    result = evaluate(container, duplicate)
    assert result["analise_transversal_conjunto"]["unique"]["resultado"] == "Não atende"
    assert "US-001" in result["analise_transversal_conjunto"]["unique"]["observacao"]
    result = evaluate(container, incomplete)
    assert result["avaliacao_criterios"]["well_formed"]["resultado"] == "Não atende"  # sem perfil
    assert result["analise_transversal_conjunto"]["uniform"]["resultado"] == "Atenção"
    assert first


def test_story_detail_and_scriba_suggestion_after_evaluation(container, project_id):
    story_id = create(container, project_id, **FULL)
    evaluate(container, story_id)
    with container.uow() as uow:
        story = StoryService(uow).detail(story_id)
    assert story["qualidade_geral"]["porcentagem"] == 92.31 and not story["qualidade_geral"]["desatualizada"]
    assert story["criterios_qus"]["avaliacao_criterios"]["unambiguous"]["resultado"] == "Atenção"
    assert story["sugestao_scriba"].startswith("Como coordenador")


def test_reevaluation_keeps_history_and_compares(container, project_id):
    story_id = create(container, project_id, **FULL)
    first = evaluate(container, story_id)
    with container.uow() as uow:
        StoryService(uow).update(story_id, {"funcionalidade": "emitir o boletim da turma em PDF"}, "ana")
        stale = StoryService(uow).detail(story_id)
    assert stale["qualidade_geral"]["desatualizada"]  # avaliação refere-se à versão anterior

    def all_ok(prompt):
        return {"avaliacao_criterios": {k: {"resultado": "Atende"} for k in qus.INDIVIDUAL_CRITERIA}, "lacunas": []}
    container.llm.handlers["AVALIACAO_QUS_INDIVIDUAL"] = all_ok
    second = evaluate(container, story_id)
    assert second["versao_historia"] == 2 and second["qualidade_geral"]["status"] == "Excelente"  # US-08 CA01
    with container.uow() as uow:
        service = QualityService(uow, container)
        history = service.history(story_id)
        comparison = service.compare(story_id)
    assert history["total"] == 2 and history["itens"][0]["id"] == first["id"]  # registro anterior preservado
    assert comparison["resolvidos"] == ["unambiguous"] and comparison["novos_defeitos"] == []  # US-08 CA02
    assert comparison["variacao_porcentagem"] == pytest.approx(7.69)


def test_compare_requires_two_evaluations(container, project_id):
    story_id = create(container, project_id, **FULL)
    evaluate(container, story_id)
    with container.uow() as uow, pytest.raises(ConflictError):
        QualityService(uow, container).compare(story_id)


def test_gaps_are_flagged_without_inventing(container, project_id):
    story_id = create(container, project_id, funcionalidade="emitir o boletim da turma")
    evaluate(container, story_id)
    with container.uow() as uow:
        defects = QualityService(uow, container).defects(story_id)["itens"]
        story = StoryService(uow).detail(story_id)
    keys = {d["chave"] for d in defects}
    assert {"qus:well_formed", "qus:unambiguous"} <= keys  # US-09 CA01
    gaps = [d for d in defects if d["tipo"] == "lacuna"]
    assert any("perfil" in d["descricao"] for d in gaps)  # US-09 CA02
    assert any("Formato do boletim" in d["descricao"] for d in gaps)
    assert story["perfil"] is None and "[A DEFINIR: perfil]" in story["historia"]  # US-09 CA03


def test_project_overview_identifies_problems(container, project_id):
    good = create(container, project_id, **FULL)
    create(container, project_id, funcionalidade="cadastrar novos alunos")
    evaluate(container, good)
    with container.uow() as uow:
        overview = QualityService(uow, container).project_overview(project_id)
    assert overview["total"] == 2 and overview["historias_com_problemas"] == 2  # US-07 CA03
    assert overview["itens"][0]["avaliada"] is False  # não avaliadas primeiro
    assert overview["distribuicao"]["Alta"] == 1 and overview["distribuicao"]["Sem avaliação"] == 1
    assert overview["qualidade_geral"] == {"porcentagem": 92.31, "status": "Alta", "historias_avaliadas": 1}


def test_llm_unavailable_raises_external_error(container, project_id):
    story_id = create(container, project_id, **FULL)
    container.llm.fail = True
    with pytest.raises(ExternalServiceError):
        evaluate(container, story_id)
