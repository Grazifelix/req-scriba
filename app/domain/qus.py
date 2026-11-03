"""Framework Quality User Story (QUS) — critérios, pontuação e faixas (regras puras).

Referência: Lucassen, G. et al. "Improving agile requirements: the Quality User Story framework
and tool", Requirements Engineering, 2016.
"""

from __future__ import annotations

import unicodedata

MEETS = "Atende"
ATTENTION = "Atenção"
FAILS = "Não atende"
RESULTS = (MEETS, ATTENTION, FAILS)

INDIVIDUAL_CRITERIA: dict[str, str] = {
    "well_formed": "Bem formada: contém ao menos um perfil e uma funcionalidade (meio).",
    "atomic": "Atômica: expressa um único requisito para uma única funcionalidade.",
    "minimal": "Mínima: contém apenas perfil, meio e fim; nada de detalhes extras.",
    "conceptually_sound": "Conceitualmente sólida: o meio expressa uma funcionalidade e o fim um propósito.",
    "problem_oriented": "Orientada ao problema: descreve o problema, não a solução técnica.",
    "unambiguous": "Não ambígua: evita termos que permitem múltiplas interpretações.",
    "full_sentence": "Frase completa: é uma frase bem escrita, gramaticalmente correta.",
    "estimatable": "Estimável: não denota um requisito grosso demais para planejar e priorizar.",
}

SET_CRITERIA: dict[str, str] = {
    "conflict_free": "Livre de conflitos: não é inconsistente com outra história.",
    "unique": "Única: não é duplicada de outra história.",
    "uniform": "Uniforme: segue o mesmo template das demais histórias.",
    "independent": "Independente: é autocontida, sem dependências inerentes.",
    "complete": "Completa: o conjunto não deixa lacunas funcionais evidentes para esta necessidade.",
}

TOTAL_CRITERIA = len(INDIVIDUAL_CRITERIA) + len(SET_CRITERIA)  # 13

QUALITY_BANDS = ((25, "Péssima"), (50, "Baixa"), (75, "Regular"), (100, "Alta"))
EXCELLENT = "Excelente"
NOT_EVALUATED = "Sem avaliação"


def percentage(criteria_met: int) -> float:
    """Porcentagem de qualidade: critérios atendidos / 13 × 100, duas casas decimais."""
    if not 0 <= criteria_met <= TOTAL_CRITERIA:
        raise ValueError(f"criteria_met deve estar entre 0 e {TOTAL_CRITERIA}")
    return round(criteria_met * 100 / TOTAL_CRITERIA, 2)


def quality_label(score: float | None) -> str:
    """Faixa: 0–24 Péssima · 25–49 Baixa · 50–74 Regular · 75–99 Alta · 100 Excelente."""
    if score is None:
        return NOT_EVALUATED
    if score >= 100:
        return EXCELLENT
    for upper, label in QUALITY_BANDS:
        if score < upper:
            return label
    return EXCELLENT  # pragma: no cover - inalcançável


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize_result(value: object) -> str:
    """Normaliza variações do LLM para Atende / Atenção / Não atende (desconhecido → Atenção)."""
    text = _strip_accents(str(value or "")).strip().lower()
    if text.startswith(("nao", "não", "viol", "falha", "reprov", "fail", "no")):
        return FAILS
    if text.startswith(("atende", "sim", "ok", "pass", "cumpre", "yes", "satisf")):
        return MEETS
    return ATTENTION


def count_met(individual: dict[str, dict], set_analysis: dict[str, dict]) -> int:
    met = sum(1 for k in INDIVIDUAL_CRITERIA if individual.get(k, {}).get("resultado") == MEETS)
    met += sum(1 for k in SET_CRITERIA if set_analysis.get(k, {}).get("resultado") == MEETS)
    return met


def criterion_description(key: str) -> str:
    return INDIVIDUAL_CRITERIA.get(key) or SET_CRITERIA.get(key) or key
