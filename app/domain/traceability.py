"""Validação estrutural da rastreabilidade (sem I/O e sem LLM).

Estrutural = o que o código consegue verificar com certeza: referências a trechos que existem, citações que
aparecem literalmente no trecho citado, campos obrigatórios e critérios ausentes. Se um trecho realmente
sustenta a afirmação é validação SEMÂNTICA, que fica para o LLM ou para a revisão humana.

As mensagens abaixo são gravadas em ``afirmacoes_sem_evidencia`` (da história ou do critério); os relatórios
as contam pelo prefixo.
"""

from __future__ import annotations

from typing import Any

INVALID_REFERENCE = "Referência a trecho inexistente nas fontes: {}"
CITATION_NOT_FOUND_IN = 'Citação não encontrada no trecho {}: "{}"'
CRITERION_WITHOUT_EVIDENCE = "Critério sem trecho das fontes vinculado."
CRITERIA_ELEMENT = "criterios_aceitacao"


def _prefix(template: str) -> str:
    return template.split("{")[0]


def _count(claims: list[str], template: str) -> int:
    prefix = _prefix(template)
    return sum(1 for claim in claims if claim.startswith(prefix))


def story_report(stories: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo da validação estrutural das histórias candidatas (antes da persistência)."""
    claims = [c for s in stories for c in s.get("afirmacoes_sem_evidencia") or []]
    return {
        "historias": len(stories),
        "historias_sem_evidencia": sum(1 for s in stories if not s.get("evidencias")),
        "referencias_invalidas": _count(claims, INVALID_REFERENCE),
        "citacoes_divergentes": _count(claims, CITATION_NOT_FOUND_IN),
        "historias_sem_perfil": sum(1 for s in stories if not s.get("perfil")),
        "historias_sem_beneficio": sum(1 for s in stories if not s.get("beneficio")),
        "criterios_descartados": sum(s.get("criterios_descartados") or 0 for s in stories),
    }


def criteria_report(stories: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo da validação estrutural dos critérios de aceitação das histórias."""
    criteria = [c for s in stories for c in s.get("criterios_aceitacao") or []]
    claims = [claim for c in criteria for claim in c.get("afirmacoes_sem_evidencia") or []]
    return {
        "criterios": len(criteria),
        "historias_sem_criterios": sum(1 for s in stories if not s.get("criterios_aceitacao")),
        "criterios_sem_evidencia": sum(1 for c in criteria if not c.get("fontes")),
        "criterios_sem_dado_quando_entao": sum(1 for c in criteria if not (c.get("quando") and c.get("entao"))),
        "referencias_invalidas": _count(claims, INVALID_REFERENCE),
        "citacoes_divergentes": _count(claims, CITATION_NOT_FOUND_IN),
    }


def criteria_gaps(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Somente as lacunas de critérios de aceitação de uma lista de informações ausentes."""
    return [e for e in entries if (e.get("elemento") or "") == CRITERIA_ELEMENT]


def without_criteria_gaps(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Informações ausentes sem as lacunas de critérios (substituídas a cada geração de critérios)."""
    return [e for e in entries if (e.get("elemento") or "") != CRITERIA_ELEMENT]
