"""Passada final de consolidação das candidatas.

O LLM vê só as candidatas (rótulos C1..Cn) e devolve OPERAÇÕES — fundir, preencher, contradições —
sem reescrever texto. O código aplica as operações de forma determinística: todo texto que chega à HU
continua vindo de uma extração que citou trechos reais, e toda evidência continua vindo do banco.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Container
from app.core.errors import AppError
from app.domain import story_format
from app.llm import parsing, prompts
from app.services.candidates import merge_candidates

logger = logging.getLogger(__name__)

CONFLICT_ELEMENT = "conflito"


def _element(entry: dict) -> str:
    return (entry.get("elemento") or "").lower().replace("í", "i")


def _add_gap(candidate: dict, entry: dict) -> None:
    gaps = candidate.setdefault("informacoes_ausentes", [])
    if entry not in gaps:
        gaps.append(entry)


def _drop_filled_gaps(candidate: dict) -> None:
    """Remove lacunas de elementos do template que já foram preenchidos."""
    candidate["informacoes_ausentes"] = [
        g for g in candidate.get("informacoes_ausentes") or []
        if not (_element(g) in story_format.ELEMENTS and candidate.get(_element(g)))
    ]


def _add_evidences(target: dict, donor: dict) -> None:
    seen = {e["trecho_id"] for e in target["evidencias"]}
    target["evidencias"] += [e for e in donor["evidencias"] if e["trecho_id"] not in seen]


def _title(candidate: dict) -> str:
    return candidate.get("titulo") or candidate["funcionalidade"]


def _apply_conflicts(labeled: dict[str, dict], conflicts: list[dict]) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for conflict in conflicts:
        group = conflict["historias"]
        for label in group:
            others = ", ".join(f"'{_title(labeled[o])}'" for o in group if o != label)
            _add_gap(labeled[label], {
                "elemento": CONFLICT_ELEMENT,
                "descricao": f"Possível contradição com a história {others}: {conflict['descricao']}",
            })
        pairs |= {frozenset((a, b)) for a in group for b in group if a != b}
    return pairs


def _apply_fills(labeled: dict[str, dict], fills: list[dict]) -> int:
    applied = 0
    for fill in fills:
        target, donor, field = labeled[fill["alvo"]], labeled[fill["de"]], fill["campo"]
        if target.get(field) or not donor.get(field):
            continue  # nunca sobrescreve conteúdo existente
        target[field] = donor[field]  # cópia literal de outra extração com fontes
        _add_evidences(target, donor)
        applied += 1
    return applied


def _merge_groups(labels: list[str], merges: list[dict], conflicts: set[frozenset[str]]) -> dict[str, list[str]]:
    """Union-find simples: nunca une conjuntos que contenham um par contraditório."""
    groups = {label: [label] for label in labels}
    owner = {label: label for label in labels}
    for merge in merges:
        for a, b in zip(merge["historias"], merge["historias"][1:]):
            ra, rb = owner[a], owner[b]
            if ra == rb or any(frozenset((x, y)) in conflicts for x in groups[ra] for y in groups[rb]):
                continue
            if labels.index(rb) < labels.index(ra):
                ra, rb = rb, ra
            groups[ra] += groups.pop(rb)
            for member in groups[ra]:
                owner[member] = ra
    return groups


def apply_operations(labeled: dict[str, dict], operations: dict[str, list[dict]], summary: dict[str, Any]) -> list[dict]:
    """Aplica contradições → preenchimentos → fusões. Retorna as candidatas na ordem original."""
    labels = list(labeled)
    conflicts = _apply_conflicts(labeled, operations["contradicoes"])
    summary["contradicoes"] += len(operations["contradicoes"])
    summary["preenchimentos"] += _apply_fills(labeled, operations["preencher"])
    groups = _merge_groups(labels, operations["fundir"], conflicts)
    result = []
    for label in labels:
        if label not in groups:
            continue  # já fundida em outra candidata
        target = labeled[label]
        for other_label in sorted(groups[label][1:], key=labels.index):
            other = labeled[other_label]
            merge_candidates(target, other)
            for gap in other.get("informacoes_ausentes") or []:
                _add_gap(target, gap)
            claims = target.setdefault("afirmacoes_sem_evidencia", [])
            claims += [c for c in other.get("afirmacoes_sem_evidencia") or [] if c not in claims]
            summary["fusoes"] += 1
        _drop_filled_gaps(target)
        result.append(target)
    return result


def _split(candidates: list[dict], budget: int) -> list[list[dict]]:
    """Divide as candidatas em grupos que cabem no prompt (sem fusão entre grupos)."""
    groups: list[list[dict]] = [[]]
    size = 0
    for index, candidate in enumerate(candidates, start=1):
        length = len(prompts.candidate_block(f"C{index}", candidate))
        if groups[-1] and size + length > budget:
            groups.append([])
            size = 0
        groups[-1].append(candidate)
        size += length
    return groups


def consolidate(container: Container, candidates: list[dict]) -> tuple[list[dict], dict[str, Any] | None]:
    """Retorna (candidatas consolidadas, resumo). Resumo None quando a passada está desligada."""
    if not container.settings.generation_consolidation:
        return candidates, None
    summary: dict[str, Any] = {"fusoes": 0, "preenchimentos": 0, "contradicoes": 0, "grupos": 0, "erro": None}
    if len(candidates) < 2:
        return candidates, summary
    groups = _split(candidates, container.settings.generation_batch_chars)
    if len(groups) > 1:
        logger.info("Consolidação dividida em %d grupos; não há fusão entre grupos.", len(groups))
    summary["grupos"] = len(groups)
    result: list[dict] = []
    for group in groups:
        if len(group) < 2:
            result.extend(group)
            continue
        labeled = {f"C{i}": c for i, c in enumerate(group, start=1)}
        try:
            data = container.llm.generate_json(prompts.SYSTEM, prompts.consolidation_prompt(list(labeled.items())))
        except AppError as exc:
            logger.warning("Passada de consolidação falhou: %s", exc.message)
            summary["erro"] = exc.message
            result.extend(group)
            continue
        operations = parsing.normalize_consolidation(data, set(labeled))
        result.extend(apply_operations(labeled, operations, summary))
    return result, summary
