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


_ANALYSIS_CODE_RE = re.compile(r"[(\[]?\b(?:RN|FN)\s*\d+\b[)\]]?", re.IGNORECASE)
_LEFTOVER_WORDS = {"atender", "atende", "ao", "aos", "o", "os", "a", "as", "e", "de", "do", "da", "requisito",
                   "requisitos", "negocio", "negócio", "funcionalidade", "conforme", "ver"}


def _benefit(value: Any) -> str | None:
    """Benefício sem códigos da análise: o modelo às vezes escreve "RN1" no lugar do texto do requisito.

    "RN1: reduzir a espera" vira "reduzir a espera"; "RN1", "RN1 e RN2" ou "atender ao RN1" viram None
    (benefício ausente, que a história sinaliza como informação faltante), nunca "para RN1".
    """
    text = clean_element(_text(value, 500))
    if not text or not _ANALYSIS_CODE_RE.search(text):
        return text
    stripped = re.sub(r"\s+", " ", _ANALYSIS_CODE_RE.sub(" ", text)).strip(" .,;:-–—")
    words = re.findall(r"\w+", stripped.lower())
    if not words or all(w in _LEFTOVER_WORDS for w in words):
        return None
    return clean_element(stripped)


STORY_COMPONENTS = ("perfil", "funcionalidade", "beneficio")
CRITERION_COMPONENTS = ("dado", "quando", "entao")
_COMPONENT_ALIASES = {"benefício": "beneficio", "então": "entao", "resultado_esperado": "entao", "resultado": "entao",
                      "condicao": "dado", "condição": "dado", "contexto": "dado", "acao": "quando", "ação": "quando",
                      "evento": "quando"}


def _components(value: Any, allowed: tuple[str, ...]) -> list[str]:
    result = []
    for item in _as_list(value):
        name = _key(item)
        name = _COMPONENT_ALIASES.get(name, name)
        if name in allowed and name not in result:
            result.append(name)
    return result


def source_entries(value: Any, valid_labels: set[str], components: tuple[str, ...] = STORY_COMPONENTS
                   ) -> tuple[list[dict[str, Any]], list[str]]:
    """Fontes citadas: aceita rótulos soltos (formato antigo) ou objetos {trecho_id, afirmacoes_sustentadas,
    citacao}. Retorna (entradas válidas sem rótulo repetido, rótulos citados que não existem no prompt)."""
    entries: dict[str, dict[str, Any]] = {}
    invalid: list[str] = []
    for item in _as_list(value):
        raw = _pick(item, "trecho_id", "rotulo", "fonte", "id", "trecho") if isinstance(item, dict) else item
        label = normalize_label(raw)
        if not label or label not in valid_labels:
            text = _text(raw, 40)
            if text and text not in invalid:
                invalid.append(text)
            continue
        entry = entries.setdefault(label, {"rotulo": label, "afirmacoes": [], "citacao": None})
        if isinstance(item, dict):
            claims = _components(_pick(item, "afirmacoes_sustentadas", "sustenta", "componentes"), components)
            entry["afirmacoes"] += [c for c in claims if c not in entry["afirmacoes"]]
            entry["citacao"] = entry["citacao"] or _text(_pick(item, "citacao", "citação", "quote", "trecho_literal"), 500)
    return list(entries.values()), invalid


def _claims(value: Any) -> list[str]:
    """Afirmações sem evidência: texto livre ou {componente, descricao} (vira "componente: descricao")."""
    claims = []
    for item in _as_list(value):
        if isinstance(item, dict):
            component = _text(_pick(item, "componente", "campo", "elemento"), 40)
            description = _text(_pick(item, "descricao", "description", "afirmacao", "motivo"), 500)
            text = f"{component}: {description}" if component and description else (description or component)
        else:
            text = _text(item, 500)
        if text and text not in claims:
            claims.append(text)
    return claims[:10]


def normalize_story(item: dict[str, Any], valid_labels: set[str]) -> dict[str, Any] | None:
    """Normaliza uma história candidata. Retorna None se não houver funcionalidade.

    "fontes" mantém só os rótulos (contrato antigo); "fontes_detalhe" traz o que cada trecho sustenta e a
    citação dele; "fontes_invalidas", os rótulos citados que não existem (validação estrutural)."""
    if not isinstance(item, dict):
        return None
    funcionalidade = clean_element(_text(_pick(item, "funcionalidade", "acao", "action", "quero", "want", "meio"), 500))
    if not funcionalidade:
        return None
    beneficio = _benefit(_pick(item, "beneficio", "benefit", "para", "so_that", "fim", "objetivo"))
    entries, invalid = source_entries(
        _pick(item, "fontes", "sources", "evidencias", "referencias", "trechos"), valid_labels
    )
    return {
        "titulo": _text(_pick(item, "titulo", "title", "nome"), 200),
        "perfil": clean_element(_text(_pick(item, "perfil", "persona", "papel", "role", "ator", "como"), 200)),
        "funcionalidade": lower_first(funcionalidade),
        "beneficio": lower_first(beneficio),
        "criterios_aceitacao": _criteria(_pick(item, "criterios_aceitacao", "acceptance_criteria", "criterios")),
        "fontes": [e["rotulo"] for e in entries],
        "fontes_detalhe": entries,
        "fontes_invalidas": invalid,
        "informacoes_ausentes": _gaps(_pick(item, "informacoes_ausentes", "lacunas", "missing_info")),
        "afirmacoes_sem_evidencia": _claims(_pick(item, "afirmacoes_sem_evidencia", "unsupported_claims")),
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
    "termo": ("termos", "glossario", "glossário", "terms"),
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
    "termo": (("termo", "term", "nome", *TEXT_KEYS), ("significado", "definicao", "definição", "meaning")),
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


SET_GAP_TYPES = ("conflito", "fluxo_incompleto", "duplicidade", "granularidade")


def _story_refs(value: Any) -> list[str]:
    return [r for r in (_code(x, "H") for x in _as_list(value)) if r]


def _set_gaps(value: Any, default_type: str = "fluxo_incompleto") -> list[dict[str, Any]]:
    gaps = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        kind = _key(_pick(item, "tipo", "type", default=default_type))
        refs = _story_refs(_pick(item, "historias", "stories", "ids", "historia"))
        description = _text(_pick(item, "descricao", "description", "motivo"), 1000)
        if refs and description:
            gaps.append({"tipo": next((t for t in SET_GAP_TYPES if kind.startswith(t[:5])), default_type),
                         "historias": refs, "descricao": description})
    return gaps


def _coverage(value: Any) -> list[dict[str, Any]]:
    coverage = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        code = _code(_pick(item, "funcionalidade_ref", "funcionalidade", "fn", "id"), "FN")
        if code:
            coverage.append({"funcionalidade_ref": code, "historias": _story_refs(_pick(item, "historias", "stories")),
                             "observacao": _text(_pick(item, "observacao", "motivo", "situacao"), 500)})
    return coverage


def normalize_extraction_result(data: dict[str, Any], valid_labels: set[str]) -> dict[str, Any]:
    """Resposta da geração de US: histórias (com "ref", "funcionalidade_ref" e "citacao" do formato antigo),
    lacunas de conjunto (inclusive os conflitos identificados, como tipo "conflito") e cobertura funcional.
    Campos ausentes viram valores vazios: respostas no formato antigo continuam válidas."""
    stories = []
    for raw in _story_items(data):
        story = normalize_story(raw, valid_labels)
        if not story:
            continue
        story["ref"] = _code(_pick(raw, "id", "codigo"), "H")
        story["funcionalidade_ref"] = _code(_pick(raw, "funcionalidade_ref", "funcionalidade_id"), "FN")
        story["citacao"] = _text(_pick(raw, "citacao", "citação", "quote", "trecho_literal"), 500)
        stories.append(story)
    gaps = _set_gaps(_pick(data, "lacunas_conjunto", "lacunas_do_conjunto", "set_gaps"))
    gaps += [g for g in _set_gaps(_pick(data, "conflitos_identificados", "conflitos"), "conflito") if g not in gaps]
    return {
        "historias": stories,
        "analise": normalize_analysis(_pick(data, "analise", "análise", "analysis"), valid_labels),
        "lacunas": gaps,
        "cobertura": _coverage(_pick(data, "cobertura_funcional", "cobertura")),
    }


# -- Auditoria semântica das evidências (VALIDACAO_EVIDENCIAS) --------------
AUDIT_RESULTS = ("COMPATIVEL", "PARCIALMENTE_COMPATIVEL", "INCOMPATIVEL", "INSUFICIENTE", "SEM_EVIDENCIA")
UNDECIDED = "INSUFICIENTE"


def audit_result(value: Any) -> str:
    """Classificação da auditoria; valor desconhecido vira INSUFICIENTE (nunca vira compatível)."""
    key = _key(value).upper().replace("Í", "I").replace("Ê", "E")
    if key.startswith("PARCIAL"):
        return "PARCIALMENTE_COMPATIVEL"
    if key.startswith("INCOMPAT"):
        return "INCOMPATIVEL"
    if key.startswith("SEM"):
        return "SEM_EVIDENCIA"
    if key.startswith("COMPAT"):
        return "COMPATIVEL"
    return UNDECIDED


def _texts(value: Any, limit: int = 500) -> list[str]:
    return [t for t in (_text(x, limit) for x in _as_list(value)) if t][:10]


def _audited_evidences(value: Any, allowed: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Trechos avaliados: só os rótulos vinculados ao item; os demais viram problema (referência indevida)."""
    entries, invalid = [], []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        label = normalize_label(_pick(item, "trecho_id", "rotulo", "fonte"))
        if label not in allowed:
            invalid.append(_text(_pick(item, "trecho_id", "rotulo", "fonte"), 40) or "?")
            continue
        entries.append({"rotulo": label, "resultado": audit_result(_pick(item, "resultado", "result")),
                        "justificativa": _text(_pick(item, "justificativa", "motivo"), 800)})
    return entries, invalid


def _audited_item(raw: dict, allowed: set[str]) -> dict[str, Any]:
    entries, invalid = _audited_evidences(_pick(raw, "evidencias_avaliadas", "evidencias"), allowed)
    problems = _texts(_pick(raw, "problemas_identificados", "problemas"))
    problems += [f"Avaliou o trecho {label}, que não está vinculado a esta afirmação." for label in invalid]
    return {
        "resultado": audit_result(_pick(raw, "resultado", "result")), "evidencias_avaliadas": entries,
        "problemas": problems, "correcao_proposta": _text(_pick(raw, "correcao_proposta", "correcao"), 1000),
        "afirmacao": _text(_pick(raw, "afirmacao"), 500),
        "componentes_sem_sustentacao": _components(_pick(raw, "componentes_sem_sustentacao"), CRITERION_COMPONENTS),
    }


def normalize_audit_result(data: dict[str, Any], items: dict[str, dict[str, set[str]]]) -> dict[str, Any]:
    """``items``: ref da história → {"componentes": {nome: rótulos}, "criterios": {ref: rótulos}}.

    Retorna, por história, os componentes e critérios avaliados pelo modelo (só ids conhecidos e só os trechos
    vinculados a cada item). O que o modelo não devolveu fica de fora; o chamador decide como registrar."""
    result: dict[str, dict[str, dict]] = {}
    for raw in _as_list(_pick(data, "auditoria", "historias", "itens")):
        if not isinstance(raw, dict):
            continue
        ref = _code(_pick(raw, "historia_id", "id", "historia"), "H")
        if ref not in items or ref in result:
            continue
        expected = items[ref]
        components = {}
        for item in _as_list(_pick(raw, "componentes")):
            name = _components(_pick(item, "componente") if isinstance(item, dict) else None, STORY_COMPONENTS)
            if name and name[0] in expected["componentes"] and name[0] not in components:
                components[name[0]] = _audited_item(item, expected["componentes"][name[0]])
        criteria = {}
        for item in _as_list(_pick(raw, "criterios_auditados", "criterios")):
            cref = str(_pick(item, "criterio_id", "id", default="")).strip().upper() if isinstance(item, dict) else ""
            if cref in expected["criterios"] and cref not in criteria:
                criteria[cref] = _audited_item(item, expected["criterios"][cref])
        result[ref] = {"componentes": components, "criterios": criteria}
    return {"historias": result, "problemas_conjunto": _texts(_pick(data, "problemas_conjunto"))}


# -- Critérios de aceitação (GERACAO_CRITERIOS_ACEITACAO) -------------------
MAX_GENERATED_CRITERIA = 8
_GHERKIN_PREFIX = {"dado": r"^(dado\s+(que\s+)?)", "quando": r"^(quando\s+)", "entao": r"^(ent[aã]o\s+)"}


def _gherkin_part(value: Any, part: str) -> str | None:
    text = _text(value, 700)
    if not text:
        return None
    return lower_first(re.sub(_GHERKIN_PREFIX[part], "", text, flags=re.IGNORECASE).strip(" .,;")) or None


def gherkin_text(dado: str | None, quando: str | None, entao: str | None) -> str | None:
    """Monta "Dado …, quando …, então …." a partir das partes (partes ausentes são omitidas)."""
    parts = [f"{word} {value}" for word, value in (("Dado", dado), ("quando", quando), ("então", entao)) if value]
    if not parts:
        return None
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


def normalize_criterion(item: Any, valid_labels: set[str]) -> dict[str, Any] | None:
    """Critério com as partes Dado/Quando/Então separadas (ou só o texto, se o modelo não separar)."""
    if isinstance(item, str):
        item = {"criterio": item}
    if not isinstance(item, dict):
        return None
    dado, quando, entao = (_gherkin_part(_pick(item, *names), part) for part, names in (
        ("dado", ("dado", "given", "contexto")), ("quando", ("quando", "when", "acao")),
        ("entao", ("entao", "então", "then", "resultado")),
    ))
    text = gherkin_text(dado, quando, entao) if (quando or entao) else None
    text = text or _text(_pick(item, "criterio", "criteria", "descricao", "texto", "gherkin"))
    if not text:
        return None
    entries, invalid = source_entries(_pick(item, "fontes", "sources", "evidencias", "trechos"), valid_labels,
                                      CRITERION_COMPONENTS)
    return {
        "titulo": _text(_pick(item, "titulo", "title", "nome"), 200), "criterio": text,
        "dado": dado, "quando": quando, "entao": entao,
        "fontes_detalhe": entries, "fontes_invalidas": invalid,
        "informacoes_ausentes": _gaps(_pick(item, "informacoes_ausentes", "lacunas")),
        "afirmacoes_sem_evidencia": _claims(_pick(item, "afirmacoes_sem_evidencia", "unsupported_claims")),
    }


def normalize_criteria_result(data: dict[str, Any], story_refs: set[str], valid_labels: set[str]) -> dict[str, Any]:
    """Resposta do prompt de critérios: {"historias": {H1: {"criterios", "lacunas"}}, "lacunas_conjunto"}.

    Histórias que o modelo não devolveu ficam de fora (o chamador as trata como falha daquela história);
    ids desconhecidos são ignorados, porque critérios nunca criam histórias."""
    stories: dict[str, dict[str, Any]] = {}
    for item in _as_list(_pick(data, "historias", "stories", "itens")):
        if not isinstance(item, dict):
            continue
        ref = _code(_pick(item, "id", "historia", "historia_id"), "H")
        if ref not in story_refs or ref in stories:
            continue
        criteria = [c for c in (normalize_criterion(x, valid_labels) for x in _as_list(
            _pick(item, "criterios_aceitacao", "acceptance_criteria", "criterios"))) if c]
        stories[ref] = {
            "criterios": criteria[:MAX_GENERATED_CRITERIA],
            "lacunas": _gaps(_pick(item, "lacunas_criterios", "lacunas", "informacoes_ausentes")),
        }
    conjunto = []
    for item in _as_list(_pick(data, "lacunas_conjunto")):
        if isinstance(item, dict) and _text(_pick(item, "descricao", "description")):
            conjunto.append({"historias": _story_refs(_pick(item, "historias")),
                             "descricao": _text(_pick(item, "descricao", "description"), 1000)})
    return {"historias": stories, "lacunas_conjunto": conjunto}


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
