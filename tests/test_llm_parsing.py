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


def test_normalize_label_keeps_context_and_candidate_prefixes():
    assert parsing.normalize_label("[F3]") == "F3" and parsing.normalize_label("f3") == "F3"
    assert parsing.normalize_label(3) == "F3" and parsing.normalize_label("Fonte 3") == "F3"
    assert parsing.normalize_label("R2") == "R2"
    assert parsing.normalize_label(4, "C") == "C4" and parsing.normalize_label("c4") == "C4"
    assert parsing.normalize_label("sem número") is None


def test_normalize_label_rejects_codes_that_are_not_sources():
    # antes "FN1" virava "F1" e a história recebia como evidência um trecho sem relação com ela
    for code in ("FN1", "RN2", "H1", "CA03"):
        assert parsing.normalize_label(code) is None


def test_extraction_result_reads_analysis_citation_and_set_gaps():
    data = {
        "analise": {
            "sistema": "Pedidos por tablet",
            "participantes": [{"nome": "Dina", "papel_na_reuniao": "gerente de projeto", "fontes": ["F1"]}],
            "usuários": [{"papel": "cliente", "fontes": "F2"}, "atendente"],
            "requisitos_negocio": [{"id": "RN1", "descricao": "reduzir a espera", "fontes": ["F2"]}],
            "funcionalidades": [{"id": "fn 1", "usuario": "cliente", "descricao": "fazer pedidos",
                                 "requisitos_negocio": ["RN1", "x"], "fontes": ["F2", "FN1"]}],
            "fora_de_escopo": [{"descricao": "escolher as unidades do piloto", "motivo": "decisão de projeto"}, {}],
        },
        "historias": [{"id": "H1", "funcionalidade_ref": "FN1", "funcionalidade": "Fazer pedidos pelo tablet",
                       "beneficio": "Reduzir a espera", "fontes": ["F2", "RN1"], "citacao": "pedir pelo tablet"},
                      {"id": "H2", "funcionalidade": "PDV integrado ao pedido"}],
        "lacunas_conjunto": [{"tipo": "Conflito", "historias": ["H1", "H2"], "descricao": "regras divergentes"},
                             {"tipo": "fluxo_incompleto", "historias": [], "descricao": "sem história"}],
    }
    result = parsing.normalize_extraction_result(data, {"F1", "F2"})
    first, second = result["historias"]
    assert (first["ref"], first["funcionalidade_ref"], first["citacao"]) == ("H1", "FN1", "pedir pelo tablet")
    assert first["funcionalidade"] == "fazer pedidos pelo tablet" and first["beneficio"] == "reduzir a espera"
    assert first["fontes"] == ["F2"]
    assert second["funcionalidade"] == "PDV integrado ao pedido"  # sigla preservada
    assert result["lacunas"] == [{"tipo": "conflito", "historias": ["H1", "H2"], "descricao": "regras divergentes"}]

    analysis = result["analise"]
    assert analysis["sistema"] == "Pedidos por tablet"
    items = {(i["tipo"], i["texto"]): i for i in analysis["itens"]}
    assert items[("participante", "Dina")]["complemento"] == "gerente de projeto"
    assert items[("usuario", "cliente")]["fontes"] == ["F2"] and ("usuario", "atendente") in items
    functionality = items[("funcionalidade", "fazer pedidos")]
    assert (functionality["codigo"], functionality["referencias"], functionality["fontes"]) == ("FN1", ["RN1"], ["F2"])
    assert items[("fora_de_escopo", "escolher as unidades do piloto")]["complemento"] == "decisão de projeto"
    assert len(analysis["itens"]) == 6  # item vazio descartado


def test_extraction_result_accepts_old_format():
    result = parsing.normalize_extraction_result({"historias": [{"funcionalidade": "emitir boletim"}]}, set())
    assert result["analise"] == {"sistema": None, "itens": []} and result["lacunas"] == []
    assert result["historias"][0]["ref"] is None and result["historias"][0]["citacao"] is None


def test_analysis_result_accepts_wrapped_or_root_object():
    analysis = {"sistema": "Portal", "usuarios": [{"papel": "secretaria", "fontes": ["F1", "F9"]}]}
    for data in ({"analise": analysis}, analysis):
        result = parsing.normalize_analysis_result(data, {"F1"})
        assert result["sistema"] == "Portal"
        assert [(i["tipo"], i["texto"], i["fontes"]) for i in result["itens"]] == [("usuario", "secretaria", ["F1"])]
    assert parsing.normalize_analysis_result({"historias": []}, set()) == {"sistema": None, "itens": []}


def test_normalize_context_requires_valid_sources():
    data = {"perfis": [{"texto": "Coordenador acompanha notas", "fontes": ["F1"]}, {"texto": "Sem fonte"}],
            "glossario": [{"termo": "Boletim", "definicao": "relatório bimestral", "sources": "F2"}],
            "objetivos": ["texto solto sem fonte"]}
    items = parsing.normalize_context(data, {"F1", "F2"})
    assert items == [
        {"tipo": "perfil", "texto": "Coordenador acompanha notas", "fontes": ["F1"]},
        {"tipo": "termo", "texto": "Boletim: relatório bimestral", "fontes": ["F2"]},
    ]


def test_normalize_consolidation_discards_invalid_operations():
    data = {
        "fundir": [{"historias": ["C1", "C9"]}, ["C2", "C3"], {"historias": ["C1"]}],
        "preencher": [
            {"alvo": "C1", "campo": "Benefício", "de": "C2"}, {"alvo": "C1", "campo": "beneficio", "de": "C1"},
            {"alvo": "C1", "campo": "funcionalidade", "de": "C2"}, {"alvo": "C7", "campo": "perfil", "de": "C2"},
        ],
        "contradicoes": [{"historias": ["C1", "C3"]}],
    }
    ops = parsing.normalize_consolidation(data, {"C1", "C2", "C3"})
    assert ops["fundir"] == [{"historias": ["C2", "C3"]}]  # C9 inexistente deixa o grupo com 1 só
    assert ops["preencher"] == [{"alvo": "C1", "campo": "beneficio", "de": "C2"}]
    assert ops["contradicoes"][0]["historias"] == ["C1", "C3"] and ops["contradicoes"][0]["descricao"]


def test_normalize_split_criteria_by_id_or_position():
    data = {"historias": [
        {"id": "H2", "criterios_aceitacao": [{"titulo": "A", "criterio": "Dado a, quando b, então c."}]},
        {"criterios_aceitacao": ["Dado x, quando y, então z."]},
        {"id": "H9", "criterios_aceitacao": ["fora do intervalo"]},
    ]}
    first, second = parsing.normalize_split_criteria(data, 2)
    assert second == [{"titulo": "A", "criterio": "Dado a, quando b, então c."}]
    assert first == [{"titulo": "Critério 1", "criterio": "Dado x, quando y, então z."}]
    assert parsing.normalize_split_criteria({}, 2) == [[], []]
