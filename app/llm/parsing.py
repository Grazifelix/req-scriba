"""Leitura tolerante e normalização das respostas JSON do LLM.

Modelos locais (Llama 4 Scout, Qwen 3.5 4B) variam nomes de chaves, formatos de lista e valores. Estas funções
convertem a saída para o contrato interno e descartam o que for inválido — nunca inventam dados.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.domain import qus
from app.domain.story_format import clean_element, lower_first

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


_LABEL_RE = re.compile(r"(?<![^\W_])([FRCfrc])?\s*(\d+)")  # início não pode vir colado a letra ou dígito


def normalize_label(value: Any, default_prefix: str = "F") -> str | None:
    """Converte "F3", "[F3]", "f3" ou 3 em "F3"; preserva os prefixos R (contexto) e C (candidata).

    Números colados a outras letras ("FN1", "RN2", "H1") não são rótulos de fonte e são rejeitados.
    """
    match = _LABEL_RE.search(str(value or ""))
    if not match:
        return None
    return f"{(match.group(1) or default_prefix).upper()}{int(match.group(2))}"


def _code(value: Any, prefix: str) -> str | None:
    """Código local da resposta (RN1, FN2, H3): aceita "FN2", "fn 2" ou 2."""
    match = re.search(r"(\d+)", str(value or ""))
    return f"{prefix}{int(match.group(1))}" if match else None


def _labels(value: Any, valid: set[str], default_prefix: str = "F") -> list[str]:
    labels = []
    for item in _as_list(value):
        label = normalize_label(item, default_prefix)
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
    beneficio = clean_element(_text(_pick(item, "beneficio", "benefit", "para", "so_that", "fim", "objetivo"), 500))
    return {
        "titulo": _text(_pick(item, "titulo", "title", "nome"), 200),
        "perfil": clean_element(_text(_pick(item, "perfil", "persona", "papel", "role", "ator", "como"), 200)),
        "funcionalidade": lower_first(funcionalidade),
        "beneficio": lower_first(beneficio),
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


# -- Análise das fontes (etapa 1 do prompt de extração) ---------------------
ANALYSIS_ITEM_TYPES: dict[str, tuple[str, ...]] = {
    "participante": ("participantes", "participants"),
    "usuario": ("usuarios", "usuários", "users", "perfis"),
    "requisito_negocio": ("requisitos_negocio", "requisitos_de_negocio", "requisitos_negócio", "business_requirements"),
    "funcionalidade": ("funcionalidades", "functional_requirements", "necessidades"),
    "fora_de_escopo": ("fora_de_escopo", "out_of_scope", "nao_requisitos"),
}
ANALYSIS_CODE_PREFIX = {"requisito_negocio": "RN", "funcionalidade": "FN"}
MAX_ANALYSIS_ITEMS = 40
TEXT_KEYS = ("descricao", "texto", "description", "text")
_ITEM_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # tipo: (chaves do texto principal, chaves do complemento)
    "participante": (("nome", "name", *TEXT_KEYS), ("papel_na_reuniao", "papel", "role", "funcao")),
    "usuario": (("papel", "perfil", "nome", "role", *TEXT_KEYS[1:]), ("descricao", "description")),
    "requisito_negocio": (TEXT_KEYS, ()),
    "funcionalidade": (TEXT_KEYS, ("usuario", "perfil", "user")),
    "fora_de_escopo": (TEXT_KEYS, ("motivo", "reason", "justificativa")),
}


def _analysis_item(kind: str, raw: Any, valid_labels: set[str]) -> dict[str, Any] | None:
    text_keys, extra_keys = _ITEM_FIELDS[kind]
    if isinstance(raw, dict):
        text = _text(_pick(raw, *text_keys), 500)
        complement = _text(_pick(raw, *extra_keys), 500) if extra_keys else None
        sources = _labels(_pick(raw, "fontes", "sources", "evidencias", "trechos"), valid_labels)
        code_prefix = ANALYSIS_CODE_PREFIX.get(kind)
        code = _code(_pick(raw, "id", "codigo", "code"), code_prefix) if code_prefix else None
        refs = _as_list(_pick(raw, "requisitos_negocio", "requisitos", "business_requirements")) if kind == "funcionalidade" else []
    else:
        text, complement, sources, code, refs = _text(raw, 500), None, [], None, []
    if not text:
        return None
    return {
        "tipo": kind, "codigo": code, "texto": text, "complemento": complement,
        "referencias": [r for r in (_code(x, "RN") for x in refs) if r], "fontes": sources,
    }


def normalize_analysis(data: Any, valid_labels: set[str]) -> dict[str, Any]:
    """Retorna {"sistema", "itens"}; itens = [{"tipo", "codigo", "texto", "complemento", "referencias", "fontes"}]."""
    if not isinstance(data, dict):
        return {"sistema": None, "itens": []}
    items = []
    for kind, names in ANALYSIS_ITEM_TYPES.items():
        for raw in _as_list(_pick(data, *names))[:MAX_ANALYSIS_ITEMS]:
            item = _analysis_item(kind, raw, valid_labels)
            if item:
                items.append(item)
    return {"sistema": _paragraphs(_pick(data, "sistema", "system", "produto", "solucao"), 2000), "itens": items}


def _paragraphs(value: Any, limit: int) -> str | None:
    """Como _text, mas preserva a separação entre parágrafos (linha em branco)."""
    text = _text(value, limit) if isinstance(value, (list, dict)) else None
    if text is not None or value is None:
        return text
    parts = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", str(value))]
    return "\n\n".join(p for p in parts if p)[:limit] or None


def normalize_analysis_result(data: dict[str, Any], valid_labels: set[str]) -> dict[str, Any]:
    """Resposta do prompt de análise: aceita {"analise": {...}} ou a análise direto no nível raiz."""
    block = _pick(data, "analise", "análise", "analysis")
    return normalize_analysis(block if isinstance(block, dict) else data, valid_labels)


def _set_gaps(value: Any) -> list[dict[str, Any]]:
    gaps = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        kind = _key(_pick(item, "tipo", "type", default="fluxo_incompleto"))
        refs = [r for r in (_code(x, "H") for x in _as_list(_pick(item, "historias", "stories", "ids"))) if r]
        description = _text(_pick(item, "descricao", "description", "motivo"), 1000)
        if refs and description:
            gaps.append({"tipo": "conflito" if kind.startswith("conf") else "fluxo_incompleto",
                         "historias": refs, "descricao": description})
    return gaps


def normalize_extraction_result(data: dict[str, Any], valid_labels: set[str]) -> dict[str, Any]:
    """Resposta completa da extração: histórias (com "ref", "funcionalidade_ref" e "citacao"), análise e
    lacunas de conjunto. Campos ausentes viram valores vazios: respostas no formato antigo continuam válidas."""
    stories = []
    for raw in _story_items(data):
        story = normalize_story(raw, valid_labels)
        if not story:
            continue
        story["ref"] = _code(_pick(raw, "id", "codigo"), "H")
        story["funcionalidade_ref"] = _code(_pick(raw, "funcionalidade_ref", "funcionalidade_id"), "FN")
        story["citacao"] = _text(_pick(raw, "citacao", "citação", "quote", "trecho_literal"), 500)
        stories.append(story)
    return {
        "historias": stories,
        "analise": normalize_analysis(_pick(data, "analise", "análise", "analysis"), valid_labels),
        "lacunas": _set_gaps(_pick(data, "lacunas_conjunto", "lacunas_do_conjunto", "set_gaps")),
    }


CONTEXT_TYPES = {
    "perfil": ("perfis", "perfil", "personas", "atores", "usuarios"),
    "objetivo": ("objetivos", "objetivo", "beneficios", "metas"),
    "termo": ("termos", "termo", "glossario", "conceitos"),
}


def _context_text(item: Any) -> str | None:
    if not isinstance(item, dict):
        return _text(item, 300)
    text = _text(_pick(item, "texto", "descricao", "description", "resumo", "text"), 300)
    term = _text(_pick(item, "termo", "nome", "perfil", "name"), 100)
    definition = _text(_pick(item, "definicao", "significado", "definition"), 250)
    if term and definition:
        return f"{term}: {definition}"
    return text or term


def normalize_context(data: dict[str, Any], valid_labels: set[str]) -> list[dict[str, Any]]:
    """Itens de contexto [{"tipo", "texto", "fontes"}]. Itens sem rótulo válido são descartados."""
    items = []
    for kind, names in CONTEXT_TYPES.items():
        for raw in _as_list(_pick(data, *names))[:15]:
            text = _context_text(raw)
            raw_labels = _pick(raw, "fontes", "sources", "evidencias", "trechos") if isinstance(raw, dict) else None
            labels = _labels(raw_labels, valid_labels)
            if text and labels:
                items.append({"tipo": kind, "texto": text, "fontes": labels})
    return items


CONSOLIDATION_FIELDS = {"perfil", "beneficio"}


def _group_labels(item: Any, valid: set[str]) -> list[str]:
    raw = _pick(item, "historias", "grupo", "candidatas", "ids", "itens") if isinstance(item, dict) else item
    return _labels(raw, valid, "C")


def normalize_consolidation(data: dict[str, Any], valid_labels: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Operações de consolidação sobre candidatas C1..Cn. Nunca contém texto de história."""
    merges = []
    for item in _as_list(_pick(data, "fundir", "fusoes", "merge", "merges")):
        group = _group_labels(item, valid_labels)
        if len(group) >= 2:
            merges.append({"historias": group})
    fills = []
    for item in _as_list(_pick(data, "preencher", "preenchimentos", "fill", "fills")):
        if not isinstance(item, dict):
            continue
        target = normalize_label(_pick(item, "alvo", "target", "historia"), "C")
        donor = normalize_label(_pick(item, "de", "doador", "origem", "from", "source"), "C")
        field = _key(_pick(item, "campo", "field", "elemento", default="")).replace("í", "i")
        if target in valid_labels and donor in valid_labels and target != donor and field in CONSOLIDATION_FIELDS:
            fills.append({"alvo": target, "campo": field, "de": donor})
    conflicts = []
    for item in _as_list(_pick(data, "contradicoes", "conflitos", "contradictions", "conflicts")):
        group = _group_labels(item, valid_labels)
        if len(group) >= 2:
            description = _text(_pick(item, "descricao", "motivo", "description"), 500) if isinstance(item, dict) else None
            conflicts.append({"historias": group, "descricao": description or "Requisitos possivelmente conflitantes."})
    return {"fundir": merges, "preencher": fills, "contradicoes": conflicts}


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
    """Retorna {"avaliacao_criterios", "lacunas"} com os 8 critérios individuais."""
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
    }


def normalize_rewrite(data: dict[str, Any]) -> str | None:
    """Reescrita sugerida ao final da avaliação de conjunto (None se vazia)."""
    return _text(_pick(data, "reescrita_sugerida", "reescrita", "rewrite", "sugestao"))


def normalize_split_criteria(data: dict[str, Any], total: int) -> list[list[dict[str, str]]]:
    """Critérios por história resultante da divisão (H1…Hn); sem id, vai para a primeira ainda sem critérios."""
    result: list[list[dict[str, str]]] = [[] for _ in range(total)]
    for item in _as_list(_pick(data, "historias", "stories", default=[])):
        if not isinstance(item, dict):
            continue
        match = re.fullmatch(r"H(\d+)", str(_pick(item, "id", "historia", default="")).strip(), re.IGNORECASE)
        index = int(match.group(1)) - 1 if match else next((i for i, c in enumerate(result) if not c), -1)
        if 0 <= index < total and not result[index]:
            result[index] = _criteria(_pick(item, "criterios_aceitacao", "acceptance_criteria", "criterios"))
    return result


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


IMPACT_RELATIONS = {"sem_relacao", "confirma", "complementa", "contradiz"}


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
