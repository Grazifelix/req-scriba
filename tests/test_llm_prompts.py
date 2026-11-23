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
