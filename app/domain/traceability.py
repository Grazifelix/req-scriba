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


# -- Validação estrutural de uma história gravada ---------------------------
ERROR, WARNING = "erro", "aviso"
STORY_COMPONENTS = ("perfil", "funcionalidade", "beneficio")
CRITERION_COMPONENTS = ("dado", "quando", "entao")
REQUIRED_EVIDENCE_FIELDS = ("tipo_fonte", "fonte_id", "nome_arquivo", "trecho", "localizacao")


def _issue(kind: str, severity: str, target: str, target_id: Any, message: str) -> dict[str, Any]:
    return {"tipo": kind, "severidade": severity, "alvo": {"tipo": target, "id": target_id}, "mensagem": message}


def _normalized(text: str | None) -> str:
    return " ".join((text or "").split())


def _evidence_issues(evidences: list[dict], chunks: dict[int, dict]) -> list[dict]:
    issues, seen = [], set()
    for e in evidences:
        missing = [f for f in REQUIRED_EVIDENCE_FIELDS if e.get(f) in (None, "", {})]
        if missing:
            issues.append(_issue("campo_obrigatorio", ERROR, "evidencia", e["id"], f"Campos ausentes: {', '.join(missing)}."))
        invalid = [c for c in e.get("afirmacoes_sustentadas") or [] if c not in STORY_COMPONENTS]
        if invalid:
            issues.append(_issue("afirmacao_invalida", ERROR, "evidencia", e["id"],
                                 f"Componentes desconhecidos: {', '.join(invalid)}."))
        chunk_id = e.get("trecho_id")
        if chunk_id is None:
            continue
        if chunk_id in seen:
            issues.append(_issue("id_duplicado", ERROR, "evidencia", e["id"],
                                 f"O trecho {chunk_id} aparece em mais de uma evidência da história."))
        seen.add(chunk_id)
        chunk = chunks.get(chunk_id)
        if chunk is None:
            issues.append(_issue("referencia_inexistente", WARNING, "evidencia", e["id"],
                                 f"O trecho {chunk_id} não existe mais nas fontes (fonte excluída ou reprocessada); "
                                 "a cópia do texto na evidência foi preservada."))
        elif _normalized(chunk["texto"]) != _normalized(e.get("trecho")):
            issues.append(_issue("citacao_divergente", WARNING, "evidencia", e["id"],
                                 f"O texto do trecho {chunk_id} mudou depois que a evidência foi registrada "
                                 "(ex.: transcrição corrigida)."))
    return issues


def _criterion_issues(criteria: list[dict], evidences: list[dict], story_version: int) -> list[dict]:
    issues = []
    evidence_ids = {e["id"] for e in evidences}
    chunk_ids = {e["trecho_id"] for e in evidences if e.get("trecho_id") is not None}
    codes: dict[str, int] = {}
    texts: dict[str, int] = {}
    for c in criteria:
        cid = c["id"]
        if c["codigo"] in codes:
            issues.append(_issue("id_duplicado", ERROR, "criterio", cid, f"Código {c['codigo']} repetido entre os vigentes."))
        codes[c["codigo"]] = cid
        key = _normalized(c["criterio"]).lower()
        if key in texts:
            issues.append(_issue("criterio_duplicado", WARNING, "criterio", cid, f"Mesmo texto do critério {texts[key]}."))
        texts[key] = cid
        missing = [f for f in ("titulo", "criterio") if not _normalized(c.get(f))]
        if missing:
            issues.append(_issue("campo_obrigatorio", ERROR, "criterio", cid, f"Campos ausentes: {', '.join(missing)}."))
        if not 1 <= (c.get("versao_historia") or 0) <= story_version:
            issues.append(_issue("vinculo_invalido", ERROR, "criterio", cid,
                                 f"Versão da história {c.get('versao_historia')} inválida (atual: {story_version})."))
        if not c.get("fontes"):
            issues.append(_issue("sem_evidencia", WARNING, "criterio", cid, "Critério sem trecho das fontes vinculado."))
        for source in c.get("fontes") or []:
            if source.get("trecho_id") not in chunk_ids or (
                    source.get("evidencia_id") is not None and source["evidencia_id"] not in evidence_ids):
                issues.append(_issue("referencia_inexistente", ERROR, "criterio", cid,
                                     f"Cita o trecho {source.get('trecho_id')}, que não é evidência desta história."))
            invalid = [p for p in source.get("afirmacoes_sustentadas") or [] if p not in CRITERION_COMPONENTS]
            if invalid:
                issues.append(_issue("afirmacao_invalida", ERROR, "criterio", cid,
                                     f"Partes desconhecidas: {', '.join(invalid)}."))
    return issues


def _version_issues(versions: list[dict], evidence_ids: set[int], criterion_ids: set[int]) -> list[dict]:
    issues, numbers = [], set()
    for v in versions:
        if v["numero"] in numbers:
            issues.append(_issue("id_duplicado", ERROR, "versao", v["numero"], "Número de versão repetido."))
        numbers.add(v["numero"])
        broken = [i for i in v.get("evidencia_ids") or [] if i not in evidence_ids]
        broken += [i for i in v.get("criterio_ids") or [] if i not in criterion_ids]
        if broken:
            issues.append(_issue("vinculo_invalido", ERROR, "versao", v["numero"],
                                 f"A versão referencia evidências ou critérios inexistentes: {broken}."))
    return issues


def _story_issues(story: dict, evidences: list[dict]) -> list[dict]:
    issues = []
    if not _normalized(story.get("funcionalidade")):
        issues.append(_issue("campo_obrigatorio", ERROR, "historia", story["id"], "História sem funcionalidade."))
    if not evidences:
        issues.append(_issue("sem_evidencia", WARNING, "historia", story["id"], "Nenhum trecho das fontes vinculado."))
        return issues
    supported = {c for e in evidences for c in e.get("afirmacoes_sustentadas") or []}
    if supported:  # evidências antigas não registram o que sustentam; sem esse dado não há o que conferir
        for component in STORY_COMPONENTS:
            if story.get(component) and component not in supported:
                issues.append(_issue("sem_evidencia", WARNING, "componente", component,
                                     f"Nenhuma evidência declara sustentar o {component} da história."))
    return issues


def validate_story(story: dict, evidences: list[dict], criteria: list[dict], all_criteria: list[dict],
                   versions: list[dict], chunks: dict[int, dict]) -> dict[str, Any]:
    """Validação estrutural de uma história gravada.

    ``criteria``: critérios vigentes; ``all_criteria``: inclusive os substituídos (vínculos das versões);
    ``chunks``: trechos atuais das fontes citados pelas evidências (id → trecho). Não julga se o trecho
    sustenta o texto (isso é a auditoria semântica)."""
    issues = _story_issues(story, evidences)
    issues += _evidence_issues(evidences, chunks)
    issues += _criterion_issues(criteria, evidences, story["versao_atual"])
    issues += _version_issues(versions, {e["id"] for e in evidences}, {c["id"] for c in all_criteria})
    errors = sum(1 for i in issues if i["severidade"] == ERROR)
    return {"historia_id": story["id"], "codigo": story.get("codigo"), "valida": errors == 0, "erros": errors,
            "avisos": len(issues) - errors, "problemas": issues}


def criteria_gaps(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Somente as lacunas de critérios de aceitação de uma lista de informações ausentes."""
    return [e for e in entries if (e.get("elemento") or "") == CRITERIA_ELEMENT]


def without_criteria_gaps(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Informações ausentes sem as lacunas de critérios (substituídas a cada geração de critérios)."""
    return [e for e in entries if (e.get("elemento") or "") != CRITERIA_ELEMENT]
