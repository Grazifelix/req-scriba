"""Critérios de aceitação como conteúdo da história (puro, sem I/O).

Os critérios ficam na tabela ``criterios_aceitacao`` (id permanente, versão da história em que entraram,
status). Na história e nos snapshots de versão eles aparecem como lista; ``criteria_content`` é a projeção
usada para comparar conteúdos e montar snapshots, sem os metadados do armazenamento.
"""

from __future__ import annotations

from typing import Any

# Campos de rastreabilidade dos critérios gerados pela IA, preservados quando presentes.
CRITERION_EXTRA_FIELDS = ("dado", "quando", "entao", "fontes", "informacoes_ausentes", "afirmacoes_sem_evidencia", "origem")
# Metadados do armazenamento: aparecem na API, mas não contam como conteúdo da história.
CRITERION_META_FIELDS = ("criterio_id", "versao_historia", "status", "situacao", "validado_na_versao",
                         "desatualizado_na_versao", "motivo_situacao")
CURRENT, REPLACED = "vigente", "substituido"
VALID, OUTDATED = "valido", "desatualizado"
ORIGINS = ("ia", "manual", "legado")


def number_criteria(criteria: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Atribui códigos CA01, CA02… pela posição, preservando título, texto e os campos de rastreabilidade."""
    numbered = []
    for c in criteria or []:
        if not (c.get("criterio") or "").strip():
            continue
        i = len(numbered) + 1
        item = {"id": f"CA{i:02d}", "titulo": (c.get("titulo") or f"Critério {i}").strip(), "criterio": c["criterio"].strip()}
        item.update({k: c[k] for k in CRITERION_EXTRA_FIELDS if c.get(k) is not None})
        numbered.append(item)
    return numbered


def criteria_content(criteria: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Projeção de conteúdo (sem metadados): dois conjuntos de critérios são iguais se ela for igual.
    As fontes entram sem ``evidencia_id`` (resolvido pelo armazenamento a partir do ``trecho_id``) e listas
    vazias contam como campo ausente."""
    result = []
    for item in number_criteria(criteria):
        if "fontes" in item:
            item["fontes"] = [{k: v for k, v in f.items() if k != "evidencia_id"} for f in item["fontes"]]
        result.append({k: v for k, v in item.items() if v != []})  # lista vazia = campo ausente
    return result
