"""US-07, US-08, US-09: avaliação QUS, reavaliação, comparação e lacunas."""

import pytest

from app.core.errors import BusinessValidationError, ConflictError, ExternalServiceError
from app.domain import qus
from app.domain.enums import StoryStatus
from app.services.quality import QualityService, run_batch_evaluation, start_batch_evaluation
from app.services.stories import StoryService
from tests.fakes import default_individual, default_set


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


def test_rewrite_comes_last_with_all_problems(container, project_id):
    story_id = create(container, project_id, **FULL)
    result = evaluate(container, story_id)
    [set_prompt] = container.llm.prompts_for("AVALIACAO_QUS_CONJUNTO")
    problems = set_prompt.split("PROBLEMAS JÁ ENCONTRADOS:")[1]
    assert "[unambiguous] Atenção: Termo 'boletim' vago." in problems
    assert "[lacuna] formato" in problems and "[atomic]" not in problems
    assert "reescrita_sugerida" not in container.llm.prompts_for("AVALIACAO_QUS_INDIVIDUAL")[0]
    assert "Nunca repita nem reformule histórias de DEMAIS HISTÓRIAS DO BACKLOG" in set_prompt
    assert result["reescrita_sugerida"].startswith("Como coordenador")


OTHER = {"perfil": "professor", "funcionalidade": "lançar as notas da turma", "beneficio": "registrar o desempenho"}
OTHER_TEXT = "Como professor, quero lançar as notas da turma, para registrar o desempenho."
TARGET_TEXT = "Como coordenador pedagógico, quero emitir o boletim da turma, para acompanhar o desempenho dos alunos."


def rewrite_with(container, rewrite):
    def handler(prompt):
        return {**default_set(prompt), "reescrita_sugerida": rewrite}
    container.llm.handlers["AVALIACAO_QUS_CONJUNTO"] = handler


def test_rewrite_drops_parts_copied_from_backlog(container, project_id):
    story_id = create(container, project_id, **FULL)
    create(container, project_id, **OTHER)
    improved = "Como coordenador pedagógico, quero emitir o boletim da turma em PDF, para acompanhar o desempenho"
    rewrite_with(container, f"H1: {improved}; H2: {OTHER_TEXT}")
    result = evaluate(container, story_id)
    assert result["reescrita_sugerida"] == improved  # sem a cópia e sem o prefixo "H1:"
    with container.uow() as uow:
        assert StoryService(uow).detail(story_id)["sugestao_scriba"] == improved


def test_rewrite_renumbers_remaining_parts(container, project_id):
    story_id = create(container, project_id, **FULL)
    create(container, project_id, **OTHER)
    first = "Como coordenador pedagógico, quero emitir o boletim da turma, para acompanhar o desempenho."
    second = "Como coordenador pedagógico, quero enviar o boletim às famílias, para mantê-las informadas."
    rewrite_with(container, f"H1: {first} H2: {OTHER_TEXT} H3: {second}")
    assert evaluate(container, story_id)["reescrita_sugerida"] == (
        f"H1: {first.rstrip('.')}; H2: {second.rstrip('.')}")


def test_rewrite_keeps_legitimate_split(container, project_id):
    story_id = create(container, project_id, **FULL)
    create(container, project_id, **OTHER)
    split = ("H1: Como coordenador pedagógico, quero emitir o boletim da turma, para acompanhar o desempenho; "
             "H2: Como coordenador pedagógico, quero enviar o boletim às famílias, para mantê-las informadas")
    rewrite_with(container, split)
    assert evaluate(container, story_id)["reescrita_sugerida"] == split


def test_rewrite_with_only_copies_is_discarded_and_clears_old_suggestion(container, project_id):
    story_id = create(container, project_id, **FULL)
    create(container, project_id, **OTHER)
    assert evaluate(container, story_id)["reescrita_sugerida"]  # sugestão anterior gravada
    rewrite_with(container, f"H1: {TARGET_TEXT}; H2: {OTHER_TEXT}")  # a HU atual sem mudança + uma cópia
    result = evaluate(container, story_id)
    assert result["reescrita_sugerida"] is None
    with container.uow() as uow:
        assert StoryService(uow).detail(story_id)["sugestao_scriba"] is None  # a antiga não fica na tela


def test_rewrite_with_typo_fix_in_a_field_is_kept(container, project_id):
    story_id = create(container, project_id, **{**FULL, "perfil": "coordenador pedagogico"})
    rewrite_with(container, [{**FULL}])
    assert evaluate(container, story_id)["reescrita_sugerida"] == TARGET_TEXT


def test_rewrite_that_only_changes_punctuation_is_discarded(container, project_id):
    """O código monta a frase no padrão: tirar a vírgula antes de "para" não muda a história."""
    story_id = create(container, project_id, **FULL)
    rewrite_with(container, TARGET_TEXT.replace("turma, para", "turma para"))
    assert evaluate(container, story_id)["reescrita_sugerida"] is None


def test_rewrite_fields_are_assembled_without_model_explanations(container, project_id):
    story_id = create(container, project_id, **FULL)
    rewrite_with(container, [{"perfil": "coordenador pedagógico", "funcionalidade": "quero emitir o boletim em PDF",
                              "beneficio": "para que eu possa acompanhar o desempenho dos alunos"}])
    assert evaluate(container, story_id)["reescrita_sugerida"] == (
        "Como coordenador pedagógico, quero emitir o boletim em PDF, para acompanhar o desempenho dos alunos.")
    assert container.llm.schemas["AVALIACAO_QUS_CONJUNTO"]["title"] == "AVALIACAO_QUS_CONJUNTO"


def test_rewrite_part_similar_to_target_is_kept(container, project_id):
    story_id = create(container, project_id, **FULL)
    create(container, project_id, **{**FULL, "funcionalidade": "emitir o boletim da turma em PDF"})  # vizinha
    reworded = "Como coordenador pedagógico, quero emitir o boletim da turma, para acompanhar o desempenho de cada aluno"
    rewrite_with(container, reworded)
    assert evaluate(container, story_id)["reescrita_sugerida"] == reworded + "."


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


def start_batch(container, project_id, story_ids):
    with container.uow() as uow:
        return start_batch_evaluation(uow, project_id, story_ids, "ana")["id"]


def job_of(container, job_id):
    with container.uow() as uow:
        return uow.jobs.get(job_id)


def test_batch_evaluation_evaluates_every_story(container, project_id):
    first = create(container, project_id, **FULL)
    second = create(container, project_id, funcionalidade="cadastrar novos alunos")
    job_id = start_batch(container, project_id, [first, second])
    run_batch_evaluation(container, job_id)
    job = job_of(container, job_id)
    assert job["status"] == "concluido" and job["tipo"] == "avaliacao_qualidade"
    assert job["resultado"]["historias_avaliadas"] == [first, second] and job["resultado"]["falhas"] == []
    with container.uow() as uow:
        assert all(uow.evaluations.latest(s) for s in (first, second))


def test_batch_evaluation_without_ids_uses_active_stories(container, project_id):
    kept = create(container, project_id, **FULL)
    rejected = create(container, project_id, funcionalidade="cadastrar novos alunos")
    with container.uow() as uow:
        StoryService(uow).set_status(rejected, StoryStatus.REJECTED)
    job_id = start_batch(container, project_id, [])
    assert job_of(container, job_id)["parametros"]["historia_ids"] == [kept]


def test_batch_evaluation_failure_of_one_story_does_not_stop_others(container, project_id):
    good = create(container, project_id, **FULL)
    bad = create(container, project_id, funcionalidade="cadastrar novos alunos")

    def fail_for_bad(prompt):
        if "cadastrar novos alunos" in prompt:
            raise ExternalServiceError("LLM indisponível (simulado).")
        return default_individual(prompt)
    container.llm.handlers["AVALIACAO_QUS_INDIVIDUAL"] = fail_for_bad
    job_id = start_batch(container, project_id, [bad, good])
    run_batch_evaluation(container, job_id)
    job = job_of(container, job_id)
    assert job["status"] == "concluido" and job["resultado"]["historias_avaliadas"] == [good]
    assert job["resultado"]["falhas"][0]["historia_id"] == bad


def test_batch_evaluation_marks_error_when_every_story_fails(container, project_id):
    story_id = create(container, project_id, **FULL)
    job_id = start_batch(container, project_id, [story_id])
    container.llm.fail = True
    run_batch_evaluation(container, job_id)
    job = job_of(container, job_id)
    assert job["status"] == "erro" and job["mensagem_erro"]


def test_batch_evaluation_rejects_invalid_stories(container, project_id):
    story_id = create(container, project_id, **FULL)
    with container.uow() as uow:
        StoryService(uow).set_status(story_id, StoryStatus.REJECTED)
    with pytest.raises(BusinessValidationError):
        start_batch(container, project_id, [story_id])  # desaprovada
    with pytest.raises(BusinessValidationError):
        start_batch(container, project_id, [999])  # inexistente
    with pytest.raises(BusinessValidationError):
        start_batch(container, project_id, [])  # projeto sem histórias ativas


def test_llm_unavailable_raises_external_error(container, project_id):
    story_id = create(container, project_id, **FULL)
    container.llm.fail = True
    with pytest.raises(ExternalServiceError):
        evaluate(container, story_id)
