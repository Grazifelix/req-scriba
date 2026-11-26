"""Prompts de avaliação QUS (US-07): critérios alinhados ao domínio."""

from app.domain import qus
from app.llm import prompts

STORY = {"codigo": "HU-01", "titulo": "Boletim", "historia": "Como coordenador, quero emitir o boletim",
         "criterios_aceitacao": []}


def test_evaluation_criteria_cover_domain_keys():
    assert set(prompts.EVAL_INDIVIDUAL_CRITERIA) == set(qus.INDIVIDUAL_CRITERIA)
    assert set(prompts.EVAL_SET_CRITERIA) == set(qus.SET_CRITERIA)


def test_individual_prompt_includes_criteria_examples():
    text = prompts.individual_evaluation_prompt(STORY)
    assert "- atomic: Atômica" in text and "Incorreto:" in text and "Correto:" in text


def test_set_prompt_only_lists_requested_criteria():
    text = prompts.set_evaluation_prompt(STORY, [], ["complete"])
    assert "- complete: Completa" in text and "- unique:" not in text


# -- Templates versionados e contrato com os JSON Schemas -------------------
import json  # noqa: E402

import pytest  # noqa: E402


def _required_keys_present(schema: dict, example, path: str = "$") -> list[str]:
    """Chaves obrigatórias do schema ausentes no exemplo de resposta do prompt (recursivo)."""
    missing = []
    if schema.get("type") == "object" and isinstance(example, dict):
        missing += [f"{path}.{k}" for k in schema.get("required", []) if k not in example]
        for key, sub in (schema.get("properties") or {}).items():
            if key in example:
                missing += _required_keys_present(sub, example[key], f"{path}.{key}")
    elif schema.get("type") == "array" and isinstance(example, list) and example:
        missing += _required_keys_present(schema.get("items") or {}, example[0], f"{path}[0]")
    return missing


def _format_example(template_name: str) -> dict:
    body = prompts.load_template(template_name)[1].template
    block = body.split("## FORMATO DA RESPOSTA")[1].strip()
    return json.JSONDecoder().raw_decode(block)[0]


@pytest.mark.parametrize("name,schema", [
    (prompts.STORY_GENERATION, prompts.STORY_SCHEMA), (prompts.CRITERIA_GENERATION, prompts.CRITERIA_SCHEMA),
])
def test_prompt_response_example_matches_its_schema(name, schema):
    assert _required_keys_present(schema, _format_example(name)) == []


def test_templates_are_versioned_and_fully_substituted():
    versions = prompts.prompt_versions()
    assert versions["GERACAO_US_QUS"] and versions["GERACAO_CRITERIOS_ACEITACAO"]
    story = prompts.story_generation_prompt([{"rotulo": "F1", "descricao": "doc", "texto": "custa $10"}])
    criteria = prompts.criteria_prompt(["[H1] Como a, quero b, para c."], [{"rotulo": "F1", "descricao": "d", "texto": "t"}])
    for text in (story, criteria):
        assert "$" not in text.replace("custa $10", "")  # nenhum placeholder sobrou
    assert "custa $10" in story  # valores com "$" não são interpretados


def test_story_prompt_keeps_qus_definitions_in_order():
    text = prompts.story_generation_prompt([{"rotulo": "F1", "descricao": "d", "texto": "t"}])
    assert text.index("12. Independente") < text.index("13. Completa")
    assert "Mínima" in text and "etapa de critérios de aceitação" in text


def test_template_loader_accepts_crlf_checkouts(tmp_path, monkeypatch):
    (tmp_path / "t.md").write_bytes(b"versao: 9.9\r\nuso: teste\r\n---\r\nlinha $x\r\nfim\r\n")
    monkeypatch.setattr(prompts, "TEMPLATES_DIR", tmp_path)
    prompts.load_template.cache_clear()
    try:
        version, template = prompts.load_template("t")
        assert version == "9.9" and template.substitute(x="1") == "linha 1\nfim\n"
    finally:
        prompts.load_template.cache_clear()
