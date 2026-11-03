from app.domain import story_format as sf


def test_build_full_story():
    assert sf.build_story_text("gestor", "exportar relatórios", "analisar custos") == (
        "Como gestor, quero exportar relatórios, para analisar custos."
    )


def test_missing_elements_use_placeholder_not_assumptions():
    text = sf.build_story_text(None, "exportar relatórios", "")
    assert text == "Como [A DEFINIR: perfil], quero exportar relatórios, para [A DEFINIR: benefício]."
    assert sf.missing_elements(None, "exportar relatórios", "") == ["perfil", "beneficio"]


def test_clean_element_discards_placeholders_and_nulls():
    assert sf.clean_element("[A DEFINIR: perfil]") is None
    assert sf.clean_element("null") is None
    assert sf.clean_element("  gestor. ") == "gestor"


def test_parse_standard_text():
    parsed = sf.parse_story_text("Como um professor, quero lançar notas, para que os alunos vejam o resultado.")
    assert parsed == {"perfil": "professor", "funcionalidade": "lançar notas", "beneficio": "os alunos vejam o resultado"}


def test_parse_without_benefit():
    parsed = sf.parse_story_text("Como professor, quero lançar notas")
    assert parsed == {"perfil": "professor", "funcionalidade": "lançar notas", "beneficio": None}


def test_parse_rejects_text_outside_template():
    assert sf.parse_story_text("O sistema deve lançar notas.") is None


def test_roundtrip_build_parse():
    text = sf.build_story_text("secretária", "matricular alunos", "manter o cadastro atualizado")
    assert sf.parse_story_text(text) == {
        "perfil": "secretária", "funcionalidade": "matricular alunos", "beneficio": "manter o cadastro atualizado"
    }


def test_missing_info_entries_describe_absence():
    entries = sf.missing_info_entries(["beneficio"])
    assert entries[0]["elemento"] == "beneficio" and "não foi mencionado" in entries[0]["descricao"]
