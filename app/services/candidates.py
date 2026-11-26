"""Histórias candidatas: extração por lote via LLM e deduplicação (usadas na geração e nas novas fontes)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.container import Container
from app.domain.text import contains_normalized, similarity
from app.domain.traceability import CITATION_NOT_FOUND_IN, INVALID_REFERENCE
from app.llm import parsing, prompts
from app.services.evidence import specs_for_entries
from app.services.retrieval import label_chunks

if TYPE_CHECKING:
    from app.services.context import MeetingContext

DUPLICATE_THRESHOLD = 0.85
EXISTING_DUPLICATE_THRESHOLD = 0.90
MAX_CRITERIA = 8
CITATION_NOT_FOUND = 'Citação não encontrada nos trechos citados: "{}"'


def story_key(candidate: dict) -> str:
    return f"{candidate.get('perfil') or ''} {candidate['funcionalidade']}"


def citation_problems(entries: list[dict], sources: dict[str, list[dict]]) -> list[str]:
    """Validação estrutural das citações por trecho: cada "citacao" precisa existir no texto do PRÓPRIO trecho."""
    problems = []
    for entry in entries:
        texts = [chunk["texto"] for chunk in sources.get(entry["rotulo"], [])]
        citation = entry.get("citacao")
        if citation and texts and not contains_normalized(" ".join(texts), citation):
            problems.append(CITATION_NOT_FOUND_IN.format(entry["rotulo"], citation))
    return problems


def _check_citation(story: dict, sources: dict[str, list[dict]]) -> None:
    """Citações por trecho (formato atual) e citação única da história (formato antigo) conferidas contra o
    texto real dos trechos; divergências e rótulos inexistentes viram afirmações sem evidência."""
    claims = story["afirmacoes_sem_evidencia"]
    claims += [p for p in citation_problems(story.get("fontes_detalhe") or [], sources) if p not in claims]
    claims += [INVALID_REFERENCE.format(label) for label in story.get("fontes_invalidas") or []]
    citation = story.pop("citacao", None)
    texts = [chunk["texto"] for label in story["fontes"] for chunk in sources.get(label, [])]
    if citation and texts and not contains_normalized(" ".join(texts), citation):
        claims.append(CITATION_NOT_FOUND.format(citation))


def _apply_set_gaps(stories: list[dict], gaps: list[dict]) -> None:
    """Lacunas de conjunto (conflito, fluxo incompleto) viram informações ausentes das histórias citadas."""
    by_ref = {s["ref"]: s for s in stories if s.get("ref")}
    for gap in gaps:
        targets = [by_ref[ref] for ref in gap["historias"] if ref in by_ref]
        for target in targets:
            others = [f"'{o.get('titulo') or o['funcionalidade']}'" for o in targets if o is not target]
            suffix = f" (relacionada a {', '.join(others)})" if others else ""
            entry = {"elemento": gap["tipo"], "descricao": gap["descricao"] + suffix}
            if entry not in target["informacoes_ausentes"]:
                target["informacoes_ausentes"].append(entry)


def labeled_sources(
    batch: list[dict], context: MeetingContext | None
) -> tuple[list[dict], dict[str, list[dict]], str | None]:
    """Rótulos F do lote + rótulos R do contexto: (blocos do prompt, rótulo → trechos, texto do contexto)."""
    blocks, mapping = label_chunks(batch)
    sources = {label: [chunk] for label, chunk in mapping.items()}
    if context and context.items:
        sources.update(context.chunk_map())
        return blocks, sources, context.text()
    return blocks, sources, None


def extract_batch(
    container: Container,
    batch: list[dict],
    context: MeetingContext | None = None,
    analysis_text: str | None = None,
    complete: bool = False,
) -> list[dict]:
    """GERACAO_US_QUS para um lote: converte as fontes citadas em especificações de evidência.

    A etapa não produz critérios de aceitação: se o modelo devolver algum, ele é descartado e contado em
    "criterios_descartados" (os critérios vêm da etapa GERACAO_CRITERIOS_ACEITACAO).
    Cada candidata mantém "funcionalidade_ref" (código FN da ANALISE DO PROJETO citado pelo modelo).
    """
    return extract_batch_result(container, batch, context, analysis_text, complete)["historias"]


def extract_batch_result(
    container: Container,
    batch: list[dict],
    context: MeetingContext | None = None,
    analysis_text: str | None = None,
    complete: bool = False,
) -> dict:
    """Como ``extract_batch``, devolvendo também as lacunas de conjunto e a cobertura funcional do lote."""
    blocks, sources, context_text = labeled_sources(batch, context)
    prompt = prompts.story_generation_prompt(blocks, context_text, analysis_text, complete)
    result = parsing.normalize_extraction_result(
        container.llm.generate_json(prompts.SYSTEM, prompt, schema=prompts.STORY_SCHEMA), set(sources)
    )
    stories = result["historias"]
    _apply_set_gaps(stories, result["lacunas"])
    for story in stories:
        _check_citation(story, sources)
        story.pop("ref", None)
        story["criterios_descartados"] = len(story.pop("criterios_aceitacao", None) or [])
        story["criterios_aceitacao"] = []
        labels = story.pop("fontes")
        entries = story.pop("fontes_detalhe", None) or [{"rotulo": label, "afirmacoes": []} for label in labels]
        story["evidencias"] = specs_for_entries(entries, sources)
        story.pop("fontes_invalidas", None)
    return {"historias": stories, "lacunas": result["lacunas"], "cobertura": result["cobertura"]}


def extract_candidates(
    container: Container, batch: list[dict], context: MeetingContext | None = None, analysis_text: str | None = None
) -> list[dict]:
    """Somente as candidatas de um lote (análise de novas fontes), sem a ligação com as funcionalidades."""
    candidates = extract_batch(container, batch, context, analysis_text)
    for candidate in candidates:
        candidate.pop("funcionalidade_ref", None)
    return candidates


def merge_candidates(target: dict, other: dict) -> None:
    """Mescla ``other`` em ``target``: une evidências, critérios e funcionalidades e preenche perfil/benefício vazios."""
    seen = {e["trecho_id"] for e in target["evidencias"]}
    target["evidencias"] += [e for e in other["evidencias"] if e["trecho_id"] not in seen]
    for field in ("perfil", "beneficio"):
        target[field] = target.get(field) or other.get(field)
    for criterion in other["criterios_aceitacao"]:
        if all(similarity(criterion["criterio"], c["criterio"]) < DUPLICATE_THRESHOLD for c in target["criterios_aceitacao"]):
            target["criterios_aceitacao"].append(criterion)
    target["criterios_aceitacao"] = target["criterios_aceitacao"][:MAX_CRITERIA]
    if other.get("funcionalidades"):
        codes = target.setdefault("funcionalidades", [])
        codes += [code for code in other["funcionalidades"] if code not in codes]


def deduplicate(candidates: list[dict]) -> list[dict]:
    """Mescla candidatas muito similares geradas em lotes diferentes."""
    unique: list[dict] = []
    for candidate in candidates:
        match = next((u for u in unique if similarity(story_key(u), story_key(candidate)) >= DUPLICATE_THRESHOLD), None)
        if match:
            merge_candidates(match, candidate)
        else:
            unique.append(candidate)
    return unique
