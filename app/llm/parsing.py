"""Leitura tolerante e normalização das respostas JSON do LLM.

Modelos pequenos (Qwen 3.5 4B) variam nomes de chaves, formatos de lista e valores. Estas funções
convertem a saída para o contrato interno e descartam o que for inválido — nunca inventam dados.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.domain import qus
from app.domain.story_format import clean_element

MAX_TEXT = 2000


def extract_json(content: str | None) -> dict[str, Any] | None:
    """Obtém um objeto JSON de um texto (aceita cercas markdown e texto ao redor)."""
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"itens": data}
    return None


def _key(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def _pick(data: dict[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {_key(k): v for k, v in data.items()}
    for name in names:
        if _key(name) in lowered and lowered[_key(name)] not in (None, ""):
            return lowered[_key(name)]
    return default


def _text(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] or None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
    return [value]


def normalize_label(value: Any) -> str | None:
    """Converte "F3", "[F3]", "f3" ou 3 em "F3"."""
    match = re.search(r"(\d+)", str(value or ""))
    return f"F{int(match.group(1))}" if match else None


def _labels(value: Any, valid: set[str]) -> list[str]:
    labels = []
    for item in _as_list(value):
        label = normalize_label(item)
        if label and label in valid and label not in labels:
            labels.append(label)
    return labels


def _criteria(value: Any) -> list[dict[str, str]]:
    result = []
    for i, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            criterion = _text(_pick(item, "criterio", "criteria", "descricao", "texto", "gherkin"))
            title = _text(_pick(item, "titulo", "title", "nome"), 200)
        else:
            criterion, title = _text(item), None
        if criterion:
            result.append({"titulo": title or f"Critério {i}", "criterio": criterion})
    return result[:10]


def _gaps(value: Any) -> list[dict[str, str]]:
    gaps = []
    for item in _as_list(value):
        if isinstance(item, dict):
            element = _text(_pick(item, "elemento", "campo", "element", "informacao"), 100)
            description = _text(_pick(item, "descricao", "description", "motivo", "detalhe"))
        else:
            element, description = None, _text(item)
        if description or element:
            gaps.append({"elemento": element or "geral", "descricao": description or element or ""})
    return gaps[:10]


def _story_items(data: dict[str, Any]) -> list[Any]:
    items = _pick(data, "historias", "historias_usuario", "user_stories", "stories", "itens")
    if items is None and _pick(data, "funcionalidade", "acao", "action"):
        items = [data]  # o modelo devolveu uma única história no nível raiz
    return items if isinstance(items, list) else []


def normalize_story(item: dict[str, Any], valid_labels: set[str]) -> dict[str, Any] | None:
    """Normaliza uma história candidata. Retorna None se não houver funcionalidade."""
    if not isinstance(item, dict):
        return None
    funcionalidade = clean_element(_text(_pick(item, "funcionalidade", "acao", "action", "quero", "want", "meio"), 500))
    if not funcionalidade:
        return None
    return {
        "titulo": _text(_pick(item, "titulo", "title", "nome"), 200),
        "perfil": clean_element(_text(_pick(item, "perfil", "persona", "papel", "role", "ator", "como"), 200)),
        "funcionalidade": funcionalidade,
        "beneficio": clean_element(_text(_pick(item, "beneficio", "benefit", "para", "so_that", "fim", "objetivo"), 500)),
        "criterios_aceitacao": _criteria(_pick(item, "criterios_aceitacao", "acceptance_criteria", "criterios")),
        "fontes": _labels(_pick(item, "fontes", "sources", "evidencias", "referencias", "trechos"), valid_labels),
        "informacoes_ausentes": _gaps(_pick(item, "informacoes_ausentes", "lacunas", "missing_info")),
        "afirmacoes_sem_evidencia": [
            t for t in (_text(x, 500) for x in _as_list(_pick(item, "afirmacoes_sem_evidencia", "unsupported_claims"))) if t
        ][:10],
    }


def normalize_extraction(data: dict[str, Any], valid_labels: set[str]) -> list[dict[str, Any]]:
    stories = (normalize_story(item, valid_labels) for item in _story_items(data))
    return [s for s in stories if s]


def _criterion_entry(value: Any, text_key: str) -> dict[str, str]:
    if isinstance(value, dict):
        result = qus.normalize_result(_pick(value, "resultado", "result", "status", "avaliacao"))
        text = _text(_pick(value, text_key, "evidencia", "observacao", "justificativa", "evidence", "reason"))
        action = _text(_pick(value, "acao", "action", "sugestao", "recomendacao"))
    else:
        result, text, action = qus.normalize_result(value), None, None
    entry = {"resultado": result, text_key: text or "Sem justificativa fornecida pelo modelo."}
    if text_key == "evidencia":
        entry["acao"] = action or ("Nenhuma ação necessária" if result == qus.MEETS else "Revise a história.")
    return entry


def _criteria_block(data: dict[str, Any], *container_names: str) -> dict[str, Any]:
    block = _pick(data, *container_names)
    source = block if isinstance(block, dict) else data
    return {_key(k): v for k, v in source.items()}


def normalize_individual_evaluation(data: dict[str, Any]) -> dict[str, Any]:
    """Retorna {"avaliacao_criterios", "lacunas", "reescrita_sugerida"} com os 8 critérios individuais."""
    block = _criteria_block(data, "avaliacao_criterios", "criterios", "criteria")
    criteria = {}
    for key in qus.INDIVIDUAL_CRITERIA:
        if key in block:
            criteria[key] = _criterion_entry(block[key], "evidencia")
        else:
            criteria[key] = {
                "resultado": qus.ATTENTION,
                "evidencia": "Critério não avaliado pelo modelo.",
                "acao": "Solicite nova avaliação ou revise manualmente.",
            }
    return {
        "avaliacao_criterios": criteria,
        "lacunas": _gaps(_pick(data, "lacunas", "informacoes_ausentes", "gaps")),
        "reescrita_sugerida": _text(_pick(data, "reescrita_sugerida", "reescrita", "rewrite", "sugestao")),
    }


def normalize_set_analysis(data: dict[str, Any], keys: list[str]) -> dict[str, dict[str, str]]:
    block = _criteria_block(data, "analise_transversal_conjunto", "analise_conjunto", "set_analysis")
    result = {}
    for key in keys:
        if key in block:
            result[key] = _criterion_entry(block[key], "observacao")
        else:
            result[key] = {"resultado": qus.ATTENTION, "observacao": "Critério não avaliado pelo modelo."}
    return result


def normalize_questions(data: dict[str, Any], valid_refs: set[str]) -> tuple[list[dict], list[dict]]:
    """Retorna (perguntas, limitacoes). Referências de defeito são rótulos D1..Dn."""
    def ref(value: Any) -> str | None:
        match = re.search(r"(\d+)", str(value or ""))
        label = f"D{int(match.group(1))}" if match else None
        return label if label in valid_refs else None

    questions = []
    for item in _as_list(_pick(data, "perguntas", "questions", "itens")):
        if not isinstance(item, dict):
            continue
        question = _text(_pick(item, "pergunta", "question", "texto"), 1000)
        defect = ref(_pick(item, "defeito", "defect", "referencia"))
        if question and defect:
            questions.append({
                "defeito": defect,
                "pergunta": question,
                "justificativa": _text(_pick(item, "justificativa", "justification", "motivo"), 1500)
                or "Pergunta necessária para esclarecer o defeito identificado.",
            })
    limitations = []
    for item in _as_list(_pick(data, "limitacoes", "limitations")):
        if isinstance(item, dict):
            limitations.append({
                "defeito": ref(_pick(item, "defeito", "defect")),
                "motivo": _text(_pick(item, "motivo", "reason", "descricao"), 1000) or "Contexto insuficiente.",
            })
        elif _text(item):
            limitations.append({"defeito": None, "motivo": _text(item, 1000)})
    return questions, limitations


IMPACT_RELATIONS = {"sem_relacao", "complementa", "contradiz"}


def normalize_impact(data: dict[str, Any], valid_labels: set[str]) -> dict[str, Any]:
    raw = _key(_pick(data, "relacao", "relation", "tipo", default="sem_relacao"))
    raw = raw.replace("ç", "c").replace("ã", "a")
    relation = next((r for r in IMPACT_RELATIONS if raw.startswith(r[:6])), "sem_relacao")
    proposal = _pick(data, "proposta", "historia_proposta", "proposal")
    return {
        "relacao": relation,
        "justificativa": _text(_pick(data, "justificativa", "motivo", "reason"), 1500) or "",
        "proposta": normalize_story(proposal, valid_labels) if isinstance(proposal, dict) else None,
        "fontes": _labels(_pick(data, "fontes", "sources", "evidencias"), valid_labels),
    }
