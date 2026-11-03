"""Único ponto que monta o JSON de resposta das entidades (regra R4)."""

from __future__ import annotations

import json
from typing import Any

from app.core.time import format_seconds
from app.domain import qus
from app.domain.enums import (
    AUDIO_STATUS_LABELS, ChangeOrigin, ChangeType, JobStatus, JobType, QuestionStatus, SourceStatus,
    SourceType, StoryStatus, SuggestionStatus, SuggestionType, label_of,
)
from app.domain.text import unified_diff

API_PREFIX = "/api/v1"
COMPARED_FIELDS = ("titulo", "perfil", "funcionalidade", "beneficio", "historia", "criterios_aceitacao")


def audio_url(audio_id: int, start: float | None = None) -> str:
    url = f"{API_PREFIX}/audios/{audio_id}/arquivo"
    return f"{url}#t={round(start, 2)}" if start is not None else url


def quality_summary(score: float | None, met: int | None = None, **extra: Any) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "porcentagem": score,
        "status": qus.quality_label(score),
        "criterios_atendidos": met,
        "total_criterios": qus.TOTAL_CRITERIA,
        **extra,
    }


# -- Projetos ---------------------------------------------------------------
def project(p: dict, counts: dict, overall: dict, backlog: list[int], approved: list[int]) -> dict:
    return {
        "id": p["id"], "titulo": p["titulo"], "descricao": p["descricao"], **counts,
        "qualidade_geral": overall, "backlog": backlog, "backlog_aprovado": approved,
        "criado_em": p["criado_em"], "atualizado_em": p["atualizado_em"],
    }


# -- Fontes -----------------------------------------------------------------
def audio(a: dict, total_segments: int) -> dict:
    status = SourceStatus(a["status"])
    return {
        "id": a["id"], "projeto_id": a["projeto_id"], "nome_arquivo": a["nome_arquivo"],
        "tipo_conteudo": a["tipo_conteudo"], "tamanho_bytes": a["tamanho_bytes"],
        "duracao_segundos": a["duracao_segundos"], "duracao_formatada": format_seconds(a["duracao_segundos"]),
        "status": status.value, "status_rotulo": AUDIO_STATUS_LABELS[status],
        "mensagem_erro": a["mensagem_erro"], "total_segmentos": total_segments,
        "url_arquivo": audio_url(a["id"]), "pode_reprocessar": status in (SourceStatus.ERROR, SourceStatus.COMPLETED),
        "criado_em": a["criado_em"], "atualizado_em": a["atualizado_em"], "processado_em": a["processado_em"],
    }


def segment(s: dict) -> dict:
    return {
        "id": s["id"], "posicao": s["posicao"], "texto": s["texto"], "texto_original": s["texto_original"],
        "editado": bool(s["editado"]), "inicio_segundos": s["inicio_segundos"], "fim_segundos": s["fim_segundos"],
        "inicio_formatado": format_seconds(s["inicio_segundos"]), "fim_formatado": format_seconds(s["fim_segundos"]),
        "url_momento": audio_url(s["audio_id"], s["inicio_segundos"]),
    }


def document(d: dict, total_chunks: int) -> dict:
    return {
        "id": d["id"], "projeto_id": d["projeto_id"], "nome_arquivo": d["nome_arquivo"],
        "tipo_conteudo": d["tipo_conteudo"], "tamanho_bytes": d["tamanho_bytes"],
        "status": d["status"], "status_rotulo": label_of(SourceStatus, d["status"]),
        "mensagem_erro": d["mensagem_erro"], "total_paginas": d["total_paginas"],
        "total_caracteres": len(d["texto_extraido"] or ""), "total_trechos": total_chunks,
        "pode_reprocessar": d["status"] in (SourceStatus.ERROR.value, SourceStatus.COMPLETED.value),
        "criado_em": d["criado_em"], "atualizado_em": d["atualizado_em"], "processado_em": d["processado_em"],
    }


def chunk(c: dict, score: float | None = None) -> dict:
    loc = c["localizacao"]
    data = {
        "trecho_id": c["id"], "tipo_fonte": c["tipo_fonte"], "fonte_id": c["fonte_id"],
        "nome_arquivo": c["nome_arquivo"], "posicao": c["posicao"], "texto": c["texto"], "localizacao": loc,
    }
    if c["tipo_fonte"] == SourceType.AUDIO.value:
        data["url_momento"] = audio_url(c["fonte_id"], loc.get("inicio_segundos"))
    if score is not None:
        data["relevancia"] = round(score, 4)
    return data


# -- Evidências -------------------------------------------------------------
def evidence(e: dict) -> dict:
    loc = e["localizacao"] or {}
    base = {
        "evidencia_id": e["id"], "tipo_fonte": e["tipo_fonte"], "nome_arquivo": e["nome_arquivo"],
        "trecho": e["trecho"], "trecho_id": e["trecho_id"], "afirmacao": e["afirmacao"],
        "origem": e["origem"], "criado_em": e["criado_em"],
    }
    if e["tipo_fonte"] == SourceType.AUDIO.value:
        start = loc.get("inicio_segundos")
        base.update({
            "audio_id": e["fonte_id"], "inicio_segundos": start, "fim_segundos": loc.get("fim_segundos"),
            "inicio_formatado": format_seconds(start), "fim_formatado": format_seconds(loc.get("fim_segundos")),
            "url_momento": audio_url(e["fonte_id"], start),
        })
    else:
        base.update({
            "documento_id": e["fonte_id"], "pagina": loc.get("pagina"),
            "inicio_caractere": loc.get("inicio_caractere"), "fim_caractere": loc.get("fim_caractere"),
        })
    return base


def split_evidences(evidences: list[dict]) -> tuple[list[dict], list[dict]]:
    presented = [evidence(e) for e in evidences]
    return (
        [e for e in presented if e["tipo_fonte"] == SourceType.AUDIO.value],
        [e for e in presented if e["tipo_fonte"] == SourceType.DOCUMENT.value],
    )


# -- Histórias --------------------------------------------------------------
def version(v: dict) -> dict:
    content = v["conteudo"]
    return {
        "numero": v["numero"], "tipo_alteracao": v["tipo_alteracao"],
        "tipo_alteracao_rotulo": label_of(ChangeType, v["tipo_alteracao"]),
        "origem_alteracao": v["origem_alteracao"], "origem_alteracao_rotulo": label_of(ChangeOrigin, v["origem_alteracao"]),
        "originada_por_ia": v["origem_alteracao"] in (
            ChangeOrigin.AI_GENERATION.value, ChangeOrigin.AI_SUGGESTION.value, ChangeOrigin.AI_SUGGESTION_EDITED.value),
        "historia": content.get("historia"), "conteudo": content, "evidencia_ids": v["evidencia_ids"],
        "responsavel": v["responsavel"], "sugestao_atualizacao_id": v["sugestao_atualizacao_id"],
        "observacao": v["observacao"], "data_hora": v["criado_em"],
    }


def evaluation(ev: dict) -> dict:
    return {
        "id": ev["id"], "historia_id": ev["historia_id"], "versao_historia": ev["versao_historia"],
        "qualidade_geral": quality_summary(ev["porcentagem"], ev["criterios_atendidos"]),
        "avaliacao_criterios": ev["avaliacao_criterios"],
        "analise_transversal_conjunto": ev["analise_transversal_conjunto"],
        "lacunas": ev["lacunas"], "reescrita_sugerida": ev["reescrita_sugerida"], "criado_em": ev["criado_em"],
    }


def story_summary(s: dict) -> dict:
    return {
        "id": s["id"], "projeto_id": s["projeto_id"], "codigo": s["codigo"], "titulo": s["titulo"],
        "historia": s["historia"], "status": s["status"], "status_rotulo": label_of(StoryStatus, s["status"]),
        "validada": s["status"] == StoryStatus.APPROVED.value,
        "qualidade_geral": quality_summary(s["qualidade_porcentagem"], None),
        "possui_informacoes_ausentes": bool(s["informacoes_ausentes"]),
        "versao_atual": s["versao_atual"], "origem": s["origem"], "atualizado_em": s["atualizado_em"],
    }


def story(s: dict, evidences: list[dict], versions: list[dict], latest_eval: dict | None, pending_updates: int) -> dict:
    audio_sources, document_sources = split_evidences(evidences)
    quality = None
    qus_block = None
    if latest_eval:
        quality = quality_summary(
            latest_eval["porcentagem"], latest_eval["criterios_atendidos"], avaliacao_id=latest_eval["id"],
            versao_historia=latest_eval["versao_historia"],
            desatualizada=latest_eval["versao_historia"] != s["versao_atual"],
        )
        qus_block = {
            "avaliacao_id": latest_eval["id"],
            "avaliacao_criterios": latest_eval["avaliacao_criterios"],
            "analise_transversal_conjunto": latest_eval["analise_transversal_conjunto"],
            "lacunas": latest_eval["lacunas"], "reescrita_sugerida": latest_eval["reescrita_sugerida"],
        }
    return {
        **story_summary(s),
        "perfil": s["perfil"], "funcionalidade": s["funcionalidade"], "beneficio": s["beneficio"],
        "qualidade_geral": quality, "criterios_aceitacao": s["criterios_aceitacao"],
        "fontes_audio": audio_sources, "fontes_documentos": document_sources,
        "possui_suporte_documental": bool(evidences),
        "afirmacoes_sem_evidencia": s["afirmacoes_sem_evidencia"],
        "informacoes_ausentes": s["informacoes_ausentes"], "criterios_qus": qus_block,
        "sugestao_scriba": s["sugestao_scriba"], "atualizacoes_pendentes": pending_updates,
        "versoes": [version(v) for v in versions], "criado_em": s["criado_em"],
    }


# -- Perguntas, sugestões e jobs -------------------------------------------
def question(q: dict, s: dict | None) -> dict:
    return {
        "id": q["id"], "projeto_id": q["projeto_id"], "historia_id": q["historia_id"],
        "historia_codigo": s["codigo"] if s else None, "historia_titulo": s["titulo"] if s else None,
        "avaliacao_id": q["avaliacao_id"], "defeito_chave": q["defeito_chave"], "tipo_defeito": q["tipo_defeito"],
        "descricao_defeito": q["descricao_defeito"], "pergunta": q["pergunta"], "justificativa": q["justificativa"],
        "status": q["status"], "status_rotulo": label_of(QuestionStatus, q["status"]),
        "disponivel_proxima_rodada": q["status"] == QuestionStatus.APPROVED.value,
        "resposta": q["resposta"], "resposta_tipo_fonte": q["resposta_tipo_fonte"],
        "resposta_fonte_id": q["resposta_fonte_id"], "editada": bool(q["editada"]),
        "criado_em": q["criado_em"], "atualizado_em": q["atualizado_em"], "respondida_em": q["respondida_em"],
    }


def comparison(current: dict | None, proposed: dict) -> dict:
    current = current or {}
    fields = []
    for name in COMPARED_FIELDS:
        a, b = current.get(name), proposed.get(name)
        changed = json.dumps(a, ensure_ascii=False, sort_keys=True) != json.dumps(b, ensure_ascii=False, sort_keys=True)
        fields.append({"campo": name, "atual": a, "proposto": b, "alterado": changed})
    return {
        "campos": fields,
        "campos_alterados": [f["campo"] for f in fields if f["alterado"]],
        "diff_texto": unified_diff(current.get("historia") or "", proposed.get("historia") or ""),
    }


def suggestion(sg: dict, s: dict | None, with_comparison: bool = True) -> dict:
    data = {
        "id": sg["id"], "projeto_id": sg["projeto_id"], "historia_id": sg["historia_id"],
        "historia_codigo": s["codigo"] if s else None, "tipo": sg["tipo"],
        "tipo_rotulo": label_of(SuggestionType, sg["tipo"]), "justificativa": sg["justificativa"],
        "conteudo_atual": sg["conteudo_atual"], "conteudo_proposto": sg["conteudo_proposto"],
        "evidencias": sg["evidencias"], "fontes": sg["fontes"], "status": sg["status"],
        "status_rotulo": label_of(SuggestionStatus, sg["status"]), "editada": bool(sg["editada"]),
        "decidido_por": sg["decidido_por"], "decidido_em": sg["decidido_em"],
        "versao_aplicada": sg["versao_aplicada"], "historia_criada_id": sg["historia_criada_id"],
        "job_id": sg["job_id"], "criado_em": sg["criado_em"], "atualizado_em": sg["atualizado_em"],
    }
    if with_comparison:
        data["comparacao"] = comparison(sg["conteudo_atual"], sg["conteudo_proposto"])
    return data


def job(j: dict) -> dict:
    return {
        "id": j["id"], "projeto_id": j["projeto_id"], "tipo": j["tipo"], "tipo_rotulo": label_of(JobType, j["tipo"]),
        "status": j["status"], "status_rotulo": label_of(JobStatus, j["status"]),
        "parametros": j["parametros"], "resultado": j["resultado"], "mensagem_erro": j["mensagem_erro"],
        "criado_em": j["criado_em"], "atualizado_em": j["atualizado_em"], "concluido_em": j["concluido_em"],
        "url_status": f"{API_PREFIX}/jobs/{j['id']}",
    }


def listing(items: list[dict]) -> dict:
    return {"itens": items, "total": len(items)}
