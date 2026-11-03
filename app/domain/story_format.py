"""Template padrão de História de Usuário: "Como [perfil], quero [funcionalidade], para [benefício]"."""

from __future__ import annotations

import re

ELEMENTS = ("perfil", "funcionalidade", "beneficio")
ELEMENT_NAMES = {"perfil": "perfil", "funcionalidade": "funcionalidade", "beneficio": "benefício"}
PLACEHOLDER = "[A DEFINIR: {}]"

_STORY_RE = re.compile(
    r"^\s*como\s+(?:um|uma|o|a)?\s*(?P<perfil>.+?)\s*,\s*(?:eu\s+)?(?:quero|gostaria(?:\s+de)?|preciso(?:\s+de)?|desejo)\s+"
    r"(?P<funcionalidade>.+?)\s*(?:,\s*|\s+)(?:para(?:\s+que)?|a\s+fim\s+de|de\s+modo\s+que)\s+(?P<beneficio>.+?)\s*\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_PARTIAL_RE = re.compile(
    r"^\s*como\s+(?:um|uma|o|a)?\s*(?P<perfil>.+?)\s*,\s*(?:eu\s+)?(?:quero|gostaria(?:\s+de)?|preciso(?:\s+de)?|desejo)\s+"
    r"(?P<funcionalidade>.+?)\s*\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def clean_element(value: str | None) -> str | None:
    """Remove espaços/pontuação final e descarta placeholders ou valores vazios."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().strip(".;,").strip()
    if not text or text.upper().startswith("[A DEFINIR") or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    return text


def build_story_text(perfil: str | None, funcionalidade: str | None, beneficio: str | None) -> str:
    """Monta o texto da HU; elementos ausentes viram "[A DEFINIR: <elemento>]" (nunca suposições)."""
    parts = {
        "perfil": clean_element(perfil) or PLACEHOLDER.format(ELEMENT_NAMES["perfil"]),
        "funcionalidade": clean_element(funcionalidade) or PLACEHOLDER.format(ELEMENT_NAMES["funcionalidade"]),
        "beneficio": clean_element(beneficio) or PLACEHOLDER.format(ELEMENT_NAMES["beneficio"]),
    }
    return f"Como {parts['perfil']}, quero {parts['funcionalidade']}, para {parts['beneficio']}."


def missing_elements(perfil: str | None, funcionalidade: str | None, beneficio: str | None) -> list[str]:
    values = {"perfil": perfil, "funcionalidade": funcionalidade, "beneficio": beneficio}
    return [name for name in ELEMENTS if clean_element(values[name]) is None]


def parse_story_text(text: str) -> dict[str, str | None] | None:
    """Extrai perfil/funcionalidade/benefício de um texto no padrão. Retorna None se fora do padrão."""
    match = _STORY_RE.match(text or "")
    if match:
        return {k: clean_element(match.group(k)) for k in ELEMENTS}
    partial = _PARTIAL_RE.match(text or "")
    if partial:
        return {
            "perfil": clean_element(partial.group("perfil")),
            "funcionalidade": clean_element(partial.group("funcionalidade")),
            "beneficio": None,
        }
    return None


def follows_template(perfil: str | None, funcionalidade: str | None, beneficio: str | None) -> bool:
    return not missing_elements(perfil, funcionalidade, beneficio)


def missing_info_entries(missing: list[str]) -> list[dict[str, str]]:
    """Entradas padrão de informações ausentes para elementos do template."""
    descriptions = {
        "perfil": "O perfil de usuário (quem precisa da funcionalidade) não foi identificado nas fontes.",
        "funcionalidade": "A funcionalidade desejada não está clara nas fontes.",
        "beneficio": "O benefício/objetivo da funcionalidade não foi mencionado nas fontes.",
    }
    return [{"elemento": name, "descricao": descriptions[name]} for name in missing]
