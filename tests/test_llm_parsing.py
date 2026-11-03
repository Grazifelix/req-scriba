from app.domain import qus
from app.llm import parsing


def test_extract_json_handles_fences_and_noise():
    assert parsing.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parsing.extract_json('Aqui está: {"a": 2} espero ter ajudado') == {"a": 2}
    assert parsing.extract_json("[1, 2]") == {"itens": [1, 2]}
    assert parsing.extract_json("não é json") is None
    assert parsing.extract_json("") is None


def test_normalize_extraction_accepts_alternative_keys():
    data = {"user_stories": [{
        "title": "Exportar", "persona": "gestor", "action": "exportar relatório", "benefit": "analisar",
        "acceptance_criteria": ["Dado X, quando Y, então Z"], "sources": "[F1], F3, F9",
    }]}
    [story] = parsing.normalize_extraction(data, {"F1", "F2", "F3"})
    assert story["perfil"] == "gestor" and story["funcionalidade"] == "exportar relatório"
    assert story["fontes"] == ["F1", "F3"]  # F9 inexistente é descartado
    assert story["criterios_aceitacao"] == [{"titulo": "Critério 1", "criterio": "Dado X, quando Y, então Z"}]


def test_normalize_extraction_drops_items_without_functionality():
    data = {"historias": [{"titulo": "Vazia", "perfil": "gestor"}, "texto solto"]}
    assert parsing.normalize_extraction(data, {"F1"}) == []


def test_normalize_extraction_keeps_null_benefit_as_missing():
    data = {"historias": [{"funcionalidade": "emitir boletim", "beneficio": "null", "perfil": None}]}
    [story] = parsing.normalize_extraction(data, set())
    assert story["beneficio"] is None and story["perfil"] is None


def test_single_story_at_root_is_accepted():
    [story] = parsing.normalize_extraction({"funcionalidade": "emitir boletim", "fontes": ["F1"]}, {"F1"})
    assert story["fontes"] == ["F1"]


def test_individual_evaluation_fills_missing_criteria_with_attention():
    data = {"avaliacao_criterios": {"Well-Formed": {"resultado": "atende", "evidencia": "ok"}, "atomic": "Não atende"}}
    result = parsing.normalize_individual_evaluation(data)
    criteria = result["avaliacao_criterios"]
    assert set(criteria) == set(qus.INDIVIDUAL_CRITERIA)
    assert criteria["well_formed"]["resultado"] == qus.MEETS
    assert criteria["atomic"]["resultado"] == qus.FAILS
    assert criteria["minimal"]["resultado"] == qus.ATTENTION
    assert criteria["minimal"]["evidencia"] == "Critério não avaliado pelo modelo."


def test_set_analysis_flat_format():
    result = parsing.normalize_set_analysis({"complete": {"result": "Atende", "reason": "ok"}}, ["complete", "independent"])
    assert result["complete"] == {"resultado": qus.MEETS, "observacao": "ok"}
    assert result["independent"]["resultado"] == qus.ATTENTION


def test_normalize_questions_validates_defect_refs():
    data = {"perguntas": [
        {"defeito": "D1", "pergunta": "Qual o formato?", "justificativa": "j"},
        {"defeito": "D7", "pergunta": "Inválida"},
        {"defeito": "2", "pergunta": "Sem justificativa"},
    ], "limitacoes": ["sem contexto"]}
    questions, limitations = parsing.normalize_questions(data, {"D1", "D2"})
    assert [q["defeito"] for q in questions] == ["D1", "D2"]
    assert questions[1]["justificativa"]
    assert limitations == [{"defeito": None, "motivo": "sem contexto"}]


def test_normalize_impact_relations():
    assert parsing.normalize_impact({"relacao": "Contradiz"}, set())["relacao"] == "contradiz"
    assert parsing.normalize_impact({"relacao": "complementa a história"}, set())["relacao"] == "complementa"
    assert parsing.normalize_impact({"relacao": "outra coisa"}, set())["relacao"] == "sem_relacao"
    result = parsing.normalize_impact(
        {"relacao": "complementa", "proposta": {"funcionalidade": "x"}, "fontes": ["F1", "F5"]}, {"F1"}
    )
    assert result["fontes"] == ["F1"] and result["proposta"]["funcionalidade"] == "x"
