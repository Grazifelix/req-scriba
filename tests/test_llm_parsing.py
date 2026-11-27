import pytest

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


def test_impact_categories_and_existing_evidence_labels():
    assert parsing.impact_category("Complementação") == "COMPLEMENTACAO"
    assert parsing.impact_category("contradição") == "CONTRADICAO" and parsing.impact_category("x") is None
    assert parsing.normalize_label("E2") == "E2"


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


def test_analysis_reads_terms_with_and_without_meaning():
    data = {"termos": [
        {"termo": "OS", "significado": "ordem de serviço aberta pelo atendente", "fontes": ["F1", "F7"]},
        {"termo": "ficha", "significado": None, "fontes": ["F2"]},
        {"significado": "sem termo"},
    ]}
    terms = parsing.normalize_analysis(data, {"F1", "F2"})["itens"]
    assert [(t["tipo"], t["texto"], t["complemento"], t["fontes"], t["codigo"]) for t in terms] == [
        ("termo", "OS", "ordem de serviço aberta pelo atendente", ["F1"], None),
        ("termo", "ficha", None, ["F2"], None),
    ]


@pytest.mark.parametrize("raw, expected", [
    ("RN1", None),
    ("rn 2", None),
    ("RN1 e RN2", None),
    ("atender ao RN1", None),
    ("RN1: Reduzir o tempo de espera", "reduzir o tempo de espera"),
    ("reduzir o tempo de espera (RN1)", "reduzir o tempo de espera"),
    ("reduzir o tempo de espera", "reduzir o tempo de espera"),
])
def test_story_benefit_never_keeps_analysis_codes(raw, expected):
    story = parsing.normalize_story({"funcionalidade": "cadastrar usuários", "beneficio": raw}, set())
    assert story["beneficio"] == expected


def test_analysis_system_keeps_paragraphs():
    data = {"sistema": "O que é.\n  Segue   aqui.\n\n\nPor que importa."}
    assert parsing.normalize_analysis(data, set())["sistema"] == "O que é. Segue aqui.\n\nPor que importa."


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


# -- Milestone 1: fontes com afirmações sustentadas e critérios Dado/Quando/Então ----------------
def test_story_sources_accept_objects_and_legacy_labels():
    story = parsing.normalize_story({"funcionalidade": "emitir boletim", "fontes": [
        {"trecho_id": "F2", "afirmacoes_sustentadas": ["Perfil", "benefício", "telas"], "citacao": "emitir o boletim"},
        "F1", {"trecho_id": "F2", "afirmacoes_sustentadas": ["funcionalidade"]}, {"trecho_id": "FN3"}, "F9",
    ]}, {"F1", "F2"})
    assert story["fontes"] == ["F2", "F1"]
    assert story["fontes_detalhe"][0] == {"rotulo": "F2", "afirmacoes": ["perfil", "beneficio", "funcionalidade"],
                                          "citacao": "emitir o boletim"}
    assert story["fontes_invalidas"] == ["FN3", "F9"]


def test_unsupported_claims_accept_component_objects():
    story = parsing.normalize_story({"funcionalidade": "x", "afirmacoes_sem_evidencia": [
        {"componente": "beneficio", "descricao": "não citado"}, "texto livre"]}, set())
    assert story["afirmacoes_sem_evidencia"] == ["beneficio: não citado", "texto livre"]


def test_extraction_result_merges_conflicts_into_set_gaps_and_reads_coverage():
    data = {"historias": [{"id": "H1", "funcionalidade": "a", "fontes": ["F1"]}],
            "lacunas_conjunto": [{"tipo": "duplicidade", "historias": ["H1"], "descricao": "repete"}],
            "conflitos_identificados": [{"historias": ["H1", "H2"], "descricao": "contradiz"}],
            "cobertura_funcional": [{"funcionalidade_ref": "FN1", "historias": ["H1"]}, {"historias": ["H1"]}]}
    result = parsing.normalize_extraction_result(data, {"F1"})
    assert [g["tipo"] for g in result["lacunas"]] == ["duplicidade", "conflito"]
    assert result["cobertura"] == [{"funcionalidade_ref": "FN1", "historias": ["H1"], "observacao": None}]


def test_criterion_composes_gherkin_and_strips_repeated_keywords():
    criterion = parsing.normalize_criterion({"titulo": "PDF", "dado": "Dado que a turma existe",
                                             "quando": "Quando emitir", "então": "Então gera o PDF.",
                                             "fontes": [{"trecho_id": "F1", "afirmacoes_sustentadas": ["resultado_esperado"]}]},
                                            {"F1"})
    assert criterion["criterio"] == "Dado a turma existe, quando emitir, então gera o PDF."
    assert criterion["fontes_detalhe"] == [{"rotulo": "F1", "afirmacoes": ["entao"], "citacao": None}]
    capitalized = parsing.normalize_criterion({"dado": "O cliente está na mesa", "quando": "O cliente pede",
                                               "entao": "O pedido vai ao PDV"}, set())
    assert capitalized["criterio"] == "Dado o cliente está na mesa, quando o cliente pede, então o pedido vai ao PDV."
    legacy = parsing.normalize_criterion("Dado x, quando y, então z.", set())
    assert legacy["criterio"] == "Dado x, quando y, então z." and legacy["dado"] is None


def test_criteria_result_ignores_unknown_stories_and_caps_criteria():
    data = {"historias": [
        {"id": "H1", "criterios_aceitacao": [{"quando": f"a{i}", "entao": "b"} for i in range(12)],
         "lacunas_criterios": [{"elemento": "regras", "descricao": "falta"}]},
        {"id": "H7", "criterios_aceitacao": [{"quando": "x", "entao": "y"}]},
    ]}
    result = parsing.normalize_criteria_result(data, {"H1", "H2"}, set())
    assert set(result["historias"]) == {"H1"}
    assert len(result["historias"]["H1"]["criterios"]) == parsing.MAX_GENERATED_CRITERIA
    assert result["historias"]["H1"]["lacunas"] == [{"elemento": "regras", "descricao": "falta"}]


# -- Reescrita sugerida da avaliação de conjunto ------------------------------
def test_rewrite_fields_are_assembled_in_backlog_template():
    data = {"reescrita_sugerida": [{"perfil": "Cliente", "funcionalidade": "Quero pagar a conta",
                                    "beneficio": "para que eu possa reduzir a espera"}]}
    assert parsing.normalize_rewrite(data) == "Como Cliente, quero pagar a conta, para reduzir a espera."


def test_rewrite_split_is_numbered_and_missing_benefit_is_not_invented():
    data = {"reescrita_sugerida": [{"perfil": "gerente", "funcionalidade": "ver as falhas", "beneficio": None},
                                   {"perfil": "gerente", "funcionalidade": "ver as paradas", "beneficio": "RN2"}]}
    assert parsing.normalize_rewrite(data) == (
        "H1: Como gerente, quero ver as falhas, para [A DEFINIR: benefício]; "
        "H2: Como gerente, quero ver as paradas, para [A DEFINIR: benefício]")


def test_rewrite_as_sentence_drops_model_explanation():
    text = ("Como Cliente, quero pagar a fatura, para reduzir o tempo. "
            "A especificação do dispositivo (tablet) deve ser definida nos critérios.")
    assert parsing.normalize_rewrite({"reescrita_sugerida": text}) == "Como Cliente, quero pagar a fatura, para reduzir o tempo."


@pytest.mark.parametrize("value", [[], "", "texto livre fora do padrão", [{"perfil": "a", "funcionalidade": ""}]])
def test_empty_or_invalid_rewrite_is_none(value):
    assert parsing.normalize_rewrite({"reescrita_sugerida": value}) is None
