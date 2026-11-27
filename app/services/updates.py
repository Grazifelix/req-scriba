"""Evolução incremental a partir de novas fontes (Milestone 3; US-13, US-14, US-15, US-18).

Fluxo do job ``analise_atualizacao``:
1. ``preparacao``: só as fontes novas; fontes já processadas por outro job do projeto são ignoradas (a menos de
   ``reprocessar``). Os trechos e seus ids são os de ``trechos_fonte``; nada das fontes anteriores muda.
2. ``recuperacao``: histórias potencialmente relacionadas a cada trecho novo (``domain/impact``): vínculo com as
   funcionalidades da análise aprovada, termos do domínio e posição no ranking de embeddings.
3. ``analise_impacto``: ANALISE_IMPACTO_FONTES em lotes (trechos novos + histórias candidatas + evidências atuais).
4. ``consolidacao``: regras do código sobre a resposta do modelo (ver ``_impact_draft``) e novas histórias sem
   duplicar o backlog.
5. ``persistencia``: cada impacto vira uma proposta PENDENTE. Nada é aplicado sem aprovação (``UpdateService``).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Container
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import ChangeOrigin, ChangeType, JobStatus, JobType, SourceType, SuggestionStatus, SuggestionType
from app.domain.impact import Candidate, find_candidates, story_text, strongest
from app.domain.text import normalize, similarity
from app.llm import parsing, prompts
from app.services import presenters
from app.services.analysis import analysis_text
from app.services.candidates import (
    EXISTING_DUPLICATE_THRESHOLD, citation_problems, deduplicate, story_key,
)
from app.services.common import require_project
from app.services.criteria import evidence_chunks
from app.services.evidence import add_evidences, evidence_spec, specs_for_entries, supported_claim
from app.services.indexing import source_key
from app.services.jobs import JobService, validate_sources
from app.services.pipeline import StageLog
from app.services.retrieval import describe_chunk, group_by_chars
from app.services.stories import apply_content_change, build_content, create_story_record
from app.domain.criteria import criteria_content
from app.services.versioning import create_version, same_content, snapshot
from app.domain.traceability import INVALID_REFERENCE

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = ("titulo", "perfil", "funcionalidade", "beneficio", "criterios_aceitacao")
COMPONENTS = ("perfil", "funcionalidade", "beneficio")
POSSIBLE_DUPLICATE_THRESHOLD = 0.60
PROCESSED_JOB_TYPES = (JobType.STORY_GENERATION.value, JobType.UPDATE_ANALYSIS.value)
CATEGORY_TYPES = {
    "CONFIRMACAO": SuggestionType.CONFIRMATION, "COMPLEMENTACAO": SuggestionType.COMPLEMENTATION,
    "MODIFICACAO": SuggestionType.MODIFICATION, "CONTRADICAO": SuggestionType.CONTRADICTION,
}


def start_analysis(uow: UnitOfWork, project_id: int, audio_ids: list[int], document_ids: list[int], author: str,
                   reprocess: bool = False) -> dict:
    """US-13 CA01/CA02: as novas fontes já estão associadas ao projeto; inicia a análise."""
    validate_sources(uow, project_id, audio_ids, document_ids)
    job = JobService(uow).create(
        project_id, JobType.UPDATE_ANALYSIS,
        {"audio_ids": audio_ids, "documento_ids": document_ids, "responsavel": author, "reprocessar": reprocess},
    )
    return presenters.job(job)


# -- 1. preparação -------------------------------------------------------------
def _keys(params: dict) -> list[str]:
    return ([source_key(SourceType.AUDIO.value, a) for a in params.get("audio_ids") or []]
            + [source_key(SourceType.DOCUMENT.value, d) for d in params.get("documento_ids") or []])


def processed_sources(uow: UnitOfWork, project_id: int, job_id: str) -> set[str]:
    """Fontes já processadas por jobs concluídos de geração ou de análise de atualização do projeto."""
    return {key for job in uow.jobs.list_by_project(project_id)
            if job["id"] != job_id and job["tipo"] in PROCESSED_JOB_TYPES and job["status"] == JobStatus.COMPLETED.value
            for key in _keys(job["parametros"])}


def _new_chunks(uow: UnitOfWork, params: dict, skip: set[str]) -> tuple[list[dict], list[str], list[str]]:
    """(trechos das fontes a analisar, chaves analisadas, chaves ignoradas por já terem sido processadas)."""
    chunks, used, skipped = [], [], []
    for kind, ids in ((SourceType.AUDIO.value, params["audio_ids"]), (SourceType.DOCUMENT.value, params["documento_ids"])):
        for source_id in ids:
            key = source_key(kind, source_id)
            if key in skip:
                skipped.append(key)
                continue
            used.append(key)
            chunks += uow.chunks.list_by_source(kind, source_id)
    return chunks, used, skipped


# -- 2. recuperação ------------------------------------------------------------
def _analysis_signals(uow: UnitOfWork, project_id: int, analysis: dict | None
                      ) -> tuple[dict[int, set[str]], dict[str, set[int]], list[str]]:
    """(trecho → códigos FN da análise aprovada, código FN → histórias, termos do domínio)."""
    chunk_fns: dict[int, set[str]] = {}
    terms: list[str] = []
    if analysis:
        for item in uow.analysis_items.list_by_analysis(analysis["id"]):
            if item["tipo"] == "funcionalidade" and item["codigo"]:
                for evidence in item["evidencias"]:
                    chunk_fns.setdefault(evidence.get("trecho_id"), set()).add(item["codigo"])
            elif item["tipo"] == "termo":
                terms.append(item["texto"])
    fn_stories: dict[str, set[int]] = {}
    for any_analysis in uow.analyses.list_by_project(project_id):  # o vínculo FN → HU pode estar em qualquer análise
        for item in uow.analysis_items.list_by_analysis(any_analysis["id"]):
            if item["tipo"] == "funcionalidade" and item["codigo"]:
                fn_stories.setdefault(item["codigo"], set()).update(item["historia_ids"])
    return chunk_fns, fn_stories, terms


def _vectors(container: Container, chunks: list[dict], stories: list[dict]) -> tuple[dict, dict, str | None]:
    """Embeddings dos trechos novos e das histórias (calculados na hora). Falha → sem o sinal semântico."""
    if not chunks or not stories:
        return {}, {}, None
    try:
        vectors = container.embedder.embed([c["texto"][:4000] for c in chunks] + [story_text(s)[:4000] for s in stories])
    except AppError as exc:
        logger.warning("Embeddings indisponíveis na recuperação de histórias: %s", exc.message)
        return {}, {}, f"Sinal semântico desativado: {exc.message}"
    chunk_vectors = {c["id"]: v for c, v in zip(chunks, vectors)}
    story_vectors = {s["id"]: v for s, v in zip(stories, vectors[len(chunks):])}
    return chunk_vectors, story_vectors, None


def retrieve(container: Container, uow: UnitOfWork, project_id: int, chunks: list[dict], stories: list[dict],
             analysis: dict | None) -> tuple[dict[int, Candidate], str | None]:
    chunk_fns, fn_stories, terms = _analysis_signals(uow, project_id, analysis)
    chunk_vectors, story_vectors, warning = _vectors(container, chunks, stories)
    candidates = find_candidates(chunks, stories, chunk_vectors, story_vectors, chunk_fns, fn_stories, terms,
                                 container.settings.impact_top_stories)
    return candidates, warning


# -- 3. análise de impacto -----------------------------------------------------
class Batch:
    """Um lote do prompt: trechos novos (F), histórias candidatas (H<id>) e as evidências atuais delas (E)."""

    def __init__(self, chunks: list[dict], candidates: list[Candidate], stories: dict[int, dict],
                 evidences: dict[int, list[dict]], criteria: dict[int, list[dict]], evidence_limit: int) -> None:
        self.new = {f"F{i}": c for i, c in enumerate(chunks, start=1)}
        self.new_by_chunk = {c["id"]: label for label, c in self.new.items()}
        self.candidates = {f"H{c.story_id}": c for c in candidates}
        self.stories = {ref: stories[c.story_id] for ref, c in self.candidates.items()}
        self.existing: dict[str, dict] = {}
        self.criteria: dict[str, dict] = {}
        self.blocks: list[str] = []
        for ref, story in self.stories.items():
            labels = []
            for evidence in evidences[story["id"]][:evidence_limit]:
                label = f"E{len(self.existing) + 1}"
                self.existing[label] = evidence
                labels.append(label)
            items = []
            for i, criterion in enumerate(criteria[story["id"]], start=1):
                self.criteria[f"{ref}-CA{i}"] = criterion
                items.append((f"{ref}-CA{i}", criterion["criterio"]))
            related = [self.new_by_chunk[cid] for cid in sorted(self.candidates[ref].chunk_ids) if cid in self.new_by_chunk]
            self.blocks.append(prompts.impact_story_block(ref, story, labels, items, related))

    def prompt(self, analysis: str | None) -> str:
        new_blocks = [{"rotulo": l, "descricao": describe_chunk(c), "texto": c["texto"]} for l, c in self.new.items()]
        existing = [{"rotulo": l, "descricao": f"evidência atual de {self._owner(l)} · {describe_chunk(c)}",
                     "texto": c["texto"]} for l, c in self._existing_chunks().items()]
        return prompts.impact_sources_prompt(new_blocks, existing, self.blocks, analysis)

    def _existing_chunks(self) -> dict[str, dict]:
        return {label: chunk for label, e in self.existing.items() for chunk in evidence_chunks([e])}

    def _owner(self, label: str) -> str:
        story_id = self.existing[label]["historia_id"]
        return next(s["codigo"] for s in self.stories.values() if s["id"] == story_id)


def build_batches(container: Container, uow: UnitOfWork, chunks: list[dict], candidates: dict[int, Candidate],
                  stories: dict[int, dict]) -> tuple[list[Batch], list[int]]:
    """Lotes por orçamento de caracteres dos trechos novos; cada um leva as histórias candidatas desses trechos
    (até ``impact_max_stories``, as de maior pontuação). Retorna (lotes, histórias que ficaram de fora)."""
    settings = container.settings
    batches, left_out = [], []
    for group in group_by_chars(chunks, max(settings.generation_batch_chars // 2, 1)):
        ids = {c["id"] for c in group}
        related = [c for c in candidates.values() if c.chunk_ids & ids]
        chosen, rest = strongest(related, settings.impact_max_stories)
        left_out += [c.story_id for c in rest]
        story_ids = [c.story_id for c in chosen]
        evidences = {i: uow.evidences.list_by_story(i) for i in story_ids}
        criteria = {i: uow.criteria.list_current(i) for i in story_ids}
        batches.append(Batch(group, chosen, stories, evidences, criteria, settings.impact_evidences_per_story))
    return batches, sorted(set(left_out) - {c.story_id for b in batches for c in b.candidates.values()})


# -- 4. consolidação: regras do código sobre a resposta ------------------------
def _proposal(current: dict, proposed: dict | None) -> dict:
    """Campo omitido ou vazio na resposta mantém o valor atual (o modelo pode devolver só o que mudou)."""
    if not proposed:
        return current
    changes = {k: proposed[k] for k in EDITABLE_FIELDS if proposed.get(k)}
    content = build_content({**current, **changes})
    if proposed.get("informacoes_ausentes"):
        content["informacoes_ausentes"] = proposed["informacoes_ausentes"]
    return content


def changed_components(current: dict, proposed: dict) -> list[str]:
    """Componentes que realmente mudam (texto normalizado), calculados pelo código, não pelo modelo."""
    return [c for c in COMPONENTS if normalize(current.get(c)) != normalize(proposed.get(c))]


def _evidence_specs(batch: Batch, labels: list[str], claims: dict[str, list[str]]) -> list[dict]:
    return [evidence_spec(batch.new[l], supported_claim(claims.get(l) or []), claims.get(l) or []) for l in labels]


def _affected_criteria(batch: Batch, ref: str, items: list[dict], category: str) -> list[dict]:
    """Critérios citados pelo modelo (ids conhecidos) + numa MODIFICACAO, todos os demais como REVISAR: critério
    antigo não vale automaticamente para a versão modificada."""
    result, seen = [], set()
    for item in items:
        criterion = batch.criteria.get(item["ref"])
        if criterion and criterion["id"] not in seen and item["ref"].startswith(f"{ref}-"):
            seen.add(criterion["id"])
            result.append({"criterio_id": criterion["id"], "codigo": criterion["codigo"], "criterio": criterion["criterio"],
                           "acao_recomendada": item["acao"], "origem": "llm"})
    if category == SuggestionType.MODIFICATION.value:
        for cref, criterion in batch.criteria.items():
            if cref.startswith(f"{ref}-") and criterion["id"] not in seen:
                result.append({"criterio_id": criterion["id"], "codigo": criterion["codigo"],
                               "criterio": criterion["criterio"], "acao_recomendada": "REVISAR", "origem": "codigo"})
    return result


def _impact_draft(batch: Batch, item: dict) -> dict:
    """Regras: CONFIRMACAO e CONTRADICAO nunca mudam o conteúdo (a alternativa do modelo fica só como
    referência); COMPLEMENTACAO que muda perfil/funcionalidade/benefício vira MODIFICACAO, e MODIFICACAO que não
    muda nada vira COMPLEMENTACAO (``categoria_original`` registra a troca)."""
    story = batch.stories[item["historia_ref"]]
    current = snapshot(story)
    category = CATEGORY_TYPES[item["categoria"]].value
    proposed, alternative = current, None
    if category in (SuggestionType.COMPLEMENTATION.value, SuggestionType.MODIFICATION.value):
        proposed = _proposal(current, item["proposta"])
        changed = changed_components(current, proposed)
        category = SuggestionType.MODIFICATION.value if changed else SuggestionType.COMPLEMENTATION.value
        if not changed:
            proposed = current
    elif category == SuggestionType.CONTRADICTION.value and item["proposta"]:
        alternative = _proposal(current, item["proposta"])["historia"]
    candidate = batch.candidates[item["historia_ref"]]
    chunk_ids = {batch.new[l]["id"] for l in item["trechos_novos"]}
    return {
        "historia_id": story["id"], "tipo": category, "justificativa": item["justificativa"]
        or "O modelo identificou impacto dos trechos novos nesta história.",
        "conteudo_atual": current, "conteudo_proposto": proposed, "versao_base": story["versao_atual"],
        "evidencias": _evidence_specs(batch, item["trechos_novos"], item["afirmacoes"]),
        "detalhes": {
            "categoria_original": CATEGORY_TYPES[item["categoria"]].value if CATEGORY_TYPES[item["categoria"]].value != category else None,
            "componentes_afetados": changed_components(current, proposed),
            "criterios_afetados": _affected_criteria(batch, item["historia_ref"], item["criterios_afetados"], category),
            "alternativa_proposta": alternative, "conflitos": [],
            "sinais_recuperacao": [s for s in candidate.signals if s["trecho_id"] in chunk_ids] or candidate.signals,
        },
    }


def _merge(drafts: list[dict]) -> list[dict]:
    """Um impacto por (história, categoria): trechos e justificativas do mesmo par são unidos."""
    merged: dict[tuple, dict] = {}
    for draft in drafts:
        key = (draft["historia_id"], draft["tipo"])
        if key not in merged:
            merged[key] = draft
            continue
        target = merged[key]
        known = {e["trecho_id"] for e in target["evidencias"]}
        target["evidencias"] += [e for e in draft["evidencias"] if e["trecho_id"] not in known]
        if draft["justificativa"] not in target["justificativa"]:
            target["justificativa"] += f" {draft['justificativa']}"
    return list(merged.values())


def _conflict_entry(batch: Batch, label: str) -> dict:
    if label in batch.new:
        chunk = batch.new[label]
        return {"rotulo": label, "origem": "fonte_nova", "trecho_id": chunk["id"], "evidencia_id": None,
                "descricao": describe_chunk(chunk), "texto": chunk["texto"]}
    evidence = batch.existing[label]
    return {"rotulo": label, "origem": "evidencia_atual", "trecho_id": evidence["trecho_id"], "evidencia_id": evidence["id"],
            "descricao": evidence["nome_arquivo"], "texto": evidence["trecho"]}


def _attach_conflicts(batch: Batch, drafts: list[dict], conflicts: list[dict]) -> int:
    """Conflitos vão para a CONTRADICAO da história; sem ela, viram uma CONTRADICAO se citarem trecho novo."""
    discarded = 0
    for conflict in conflicts:
        story = batch.stories[conflict["historia_ref"]]
        entry = {"descricao": conflict["descricao"], "impacto": conflict["impacto"],
                 "trechos": [_conflict_entry(batch, l) for l in conflict["trechos"]]}
        target = next((d for d in drafts if d["historia_id"] == story["id"]
                       and d["tipo"] == SuggestionType.CONTRADICTION.value), None)
        new_labels = [l for l in conflict["trechos"] if l in batch.new]
        if target is None and not new_labels:
            discarded += 1
            continue
        if target is None:
            target = _impact_draft(batch, {"historia_ref": conflict["historia_ref"], "categoria": "CONTRADICAO",
                                           "trechos_novos": new_labels, "afirmacoes": {}, "proposta": None,
                                           "justificativa": conflict["descricao"], "criterios_afetados": []})
            drafts.append(target)
        target["detalhes"]["conflitos"].append(entry)
    return discarded


def _new_story(batch: Batch, story: dict) -> dict | None:
    sources = {label: [chunk] for label, chunk in batch.new.items()}
    entries = story.pop("fontes_detalhe") or [{"rotulo": l, "afirmacoes": []} for l in story["fontes"]]
    evidences = specs_for_entries(entries, sources)
    if not evidences:
        return None
    claims = story["afirmacoes_sem_evidencia"]
    claims += [p for p in citation_problems(entries, sources) if p not in claims]
    claims += [INVALID_REFERENCE.format(label) for label in story.pop("fontes_invalidas", [])]
    return {**story, "evidencias": evidences, "criterios_aceitacao": []}


def _new_need_drafts(stories: list[dict], existing: list[dict]) -> tuple[list[dict], list[dict]]:
    """Novas necessidades: sem duplicar o backlog (≥ 0,90 com uma HU ativa → descartada); 0,60–0,90 → possível
    duplicidade registrada para o analista. Ids temporários NOVA-1, NOVA-2… por job (o definitivo vem ao aprovar)."""
    drafts, discarded = [], []
    for story in deduplicate(stories):
        scores = sorted(((similarity(f"{s['perfil'] or ''} {s['funcionalidade'] or ''}", story_key(story)), s)
                         for s in existing), key=lambda x: x[0], reverse=True)
        if scores and scores[0][0] >= EXISTING_DUPLICATE_THRESHOLD:
            discarded.append({"funcionalidade": story["funcionalidade"], "historia_existente": scores[0][1]["codigo"]})
            continue
        content = build_content(story)
        drafts.append({
            "historia_id": None, "tipo": SuggestionType.NEW_NEED.value, "conteudo_atual": None, "versao_base": None,
            "justificativa": "Necessidade funcional dos trechos novos sem correspondência nas histórias existentes.",
            "conteudo_proposto": {**content, "informacoes_ausentes": story["informacoes_ausentes"],
                                  "afirmacoes_sem_evidencia": story["afirmacoes_sem_evidencia"]},
            "evidencias": story["evidencias"],
            "detalhes": {"id_temporario": f"NOVA-{len(drafts) + 1}", "funcionalidade_ref": story.get("funcionalidade_ref"),
                         "possiveis_duplicidades": [
                             {"historia_id": s["id"], "codigo": s["codigo"], "similaridade": round(v, 2)}
                             for v, s in scores if v >= POSSIBLE_DUPLICATE_THRESHOLD][:3]},
        })
    return drafts, discarded


def analyze_batch(container: Container, batch: Batch, analysis: str | None) -> dict:
    data = container.llm.generate_json(prompts.SYSTEM, batch.prompt(analysis), schema=prompts.IMPACT_SCHEMA)
    result = parsing.normalize_impact_sources_result(data, set(batch.stories), set(batch.new), set(batch.existing))
    drafts = _merge([_impact_draft(batch, item) for item in result["impactos"]])
    discarded = result["descartados"] + _attach_conflicts(batch, drafts, result["conflitos"])
    stories = [s for s in (_new_story(batch, story) for story in result["novas_historias"]) if s]
    discarded += len(result["novas_historias"]) - len(stories)
    unaffected = [batch.stories[r]["codigo"] for r in result["historias_nao_afetadas"]]
    return {"drafts": drafts, "new_stories": stories, "discarded": discarded, "unaffected": unaffected}


# -- job -----------------------------------------------------------------------
def _progress(container: Container, job_id: str, stages: list[dict]) -> None:
    with container.uow() as uow:
        uow.jobs.update(job_id, {"resultado": {"etapas": stages}, "atualizado_em": utc_now_iso()})


def _summary(created: list[dict]) -> dict[str, int]:
    counts = {t.value: 0 for t in (SuggestionType.CONFIRMATION, SuggestionType.COMPLEMENTATION,
                                   SuggestionType.MODIFICATION, SuggestionType.CONTRADICTION, SuggestionType.NEW_NEED)}
    for draft in created:
        counts[draft["tipo"]] += 1
    return counts


def _analyze_all(container: Container, batches: list[Batch], analysis: str | None) -> tuple[list[dict], list[dict], dict]:
    drafts, new_stories, failures, info = [], [], [], {"descartados": 0, "nao_afetadas": []}
    for index, batch in enumerate(batches, start=1):
        try:
            result = analyze_batch(container, batch, analysis)
        except AppError as exc:
            logger.warning("Lote %d da análise de impacto falhou: %s", index, exc.message)
            failures.append({"lote": index, "erro": exc.message})
            continue
        drafts += result["drafts"]
        new_stories += result["new_stories"]
        info["descartados"] += result["discarded"]
        info["nao_afetadas"] += result["unaffected"]
    if batches and len(failures) == len(batches):
        raise AppError("Todos os lotes da análise de impacto falharam.", {"falhas": failures})
    return drafts, new_stories, {**info, "falhas": failures}


def run_update_analysis(container: Container, job_id: str) -> None:
    with container.uow() as uow:
        job = uow.jobs.get(job_id)
        if not job:
            return
        JobService(uow).mark(job_id, JobStatus.PROCESSING)
    params, project_id = job["parametros"], job["projeto_id"]
    stages = StageLog(on_update=lambda current: _progress(container, job_id, current))
    try:
        with stages.run("preparacao") as stage:
            with container.uow() as uow:
                validate_sources(uow, project_id, params["audio_ids"], params["documento_ids"])
                skip = set() if params.get("reprocessar") else processed_sources(uow, project_id, job_id)
                chunks, used, skipped = _new_chunks(uow, params, skip)
                stories = {s["id"]: s for s in uow.stories.list_active(project_id)}
                analysis = uow.analyses.latest_reviewed(project_id)
                text = analysis_text(uow, analysis, for_stories=True)
            stage.update(trechos_novos=len(chunks), fontes_ignoradas=len(skipped), historias=len(stories))
        if not chunks:
            stages.skip("analise_impacto", "nenhum trecho novo")
            _complete(container, job_id, stages, [], {"fontes_analisadas": used, "fontes_ignoradas": skipped, "mensagem":
                      "Todas as fontes selecionadas já foram processadas; use reprocessar para analisá-las de novo."
                      if skipped else "As fontes selecionadas não têm trechos."})
            return
        with stages.run("recuperacao") as stage:
            with container.uow() as uow:
                candidates, warning = retrieve(container, uow, project_id, chunks, list(stories.values()), analysis)
                batches, left_out = build_batches(container, uow, chunks, candidates, stories)
            stage.update(historias_candidatas=len(candidates), lotes=len(batches), aviso=warning)
        with stages.run("analise_impacto") as stage:
            drafts, new_stories, info = _analyze_all(container, batches, text)
            stage.update(impactos=len(drafts), novas_historias=len(new_stories), falhas=len(info["falhas"]))
        with stages.run("consolidacao") as stage:
            needs, duplicates = _new_need_drafts(new_stories, list(stories.values()))
            reclassified = sum(1 for d in drafts if d["detalhes"].get("categoria_original"))
            stage.update(reclassificadas=reclassified, novas_descartadas_por_duplicidade=len(duplicates))
        with stages.run("persistencia") as stage:
            created = _persist(container, project_id, job_id, used, drafts + needs)
            stage.update(sugestoes_criadas=len(created))
        _complete(container, job_id, stages, drafts + needs, {
            "sugestoes_criadas": created, "fontes_analisadas": used, "fontes_ignoradas": skipped,
            "trechos_novos": len(chunks), "historias_analisadas": len({c.story_id for b in batches for c in b.candidates.values()}),
            "historias_candidatas": len(candidates), "historias_fora_do_limite": left_out,
            "historias_nao_afetadas": sorted(set(info["nao_afetadas"])), "reclassificadas": reclassified,
            "descartados": info["descartados"], "novas_descartadas_por_duplicidade": duplicates,
            "falhas": info["falhas"], "aviso_recuperacao": warning,
        })
    except AppError as exc:
        _fail(container, job_id, exc.message, {"detalhes": exc.details, "etapas": stages.stages})
    except Exception as exc:
        logger.exception("Falha inesperada no job %s", job_id)
        _fail(container, job_id, f"Erro inesperado: {type(exc).__name__}", {"etapas": stages.stages})


def _persist(container: Container, project_id: int, job_id: str, keys: list[str], drafts: list[dict]) -> list[int]:
    now = utc_now_iso()
    with container.uow() as uow:
        return [uow.suggestions.insert({
            "projeto_id": project_id, **draft, "fontes": keys, "status": SuggestionStatus.PENDING.value, "editada": 0,
            "job_id": job_id, "criado_em": now, "atualizado_em": now,
        }) for draft in drafts]


def _complete(container: Container, job_id: str, stages: StageLog, drafts: list[dict], result: dict) -> None:
    summary = _summary(drafts)
    created = result.get("sugestoes_criadas", [])
    message = result.pop("mensagem", None)
    if message is None and not created:
        message = "Nenhum impacto ou nova necessidade identificado nas novas fontes."
    with container.uow() as uow:
        JobService(uow).mark(job_id, JobStatus.COMPLETED, result={
            "sugestoes_criadas": created, "total": len(created), "por_categoria": summary, **result,
            "etapas": stages.stages, "duracao_total_s": stages.total_seconds(),
            "versoes_prompt": prompts.prompt_versions(), "mensagem": message,
        })


def _fail(container: Container, job_id: str, message: str, result: dict | None = None) -> None:
    with container.uow() as uow:
        JobService(uow).mark(job_id, JobStatus.ERROR, result=result, error=message)


# -- decisão humana --------------------------------------------------------------
class UpdateService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def _get(self, suggestion_id: int) -> dict:
        suggestion = self.uow.suggestions.get(suggestion_id)
        if not suggestion:
            raise NotFoundError.for_entity("Sugestão de atualização", suggestion_id)
        return suggestion

    def _present(self, suggestion: dict, with_comparison: bool = True) -> dict:
        story = self.uow.stories.get(suggestion["historia_id"]) if suggestion["historia_id"] else None
        return presenters.suggestion(suggestion, story, with_comparison)

    def _require_pending(self, suggestion: dict) -> None:
        if suggestion["status"] != SuggestionStatus.PENDING.value:
            raise ConflictError(f"A sugestão já foi decidida (status '{suggestion['status']}').")

    def list(self, project_id: int, status: str | None = None, story_id: int | None = None) -> dict:
        require_project(self.uow, project_id)
        items = self.uow.suggestions.list_by_project(project_id, status, story_id)
        return presenters.listing([self._present(s, with_comparison=False) for s in items])

    def get(self, suggestion_id: int) -> dict:
        """US-14 CA02: comparação entre conteúdo atual e proposto."""
        return self._present(self._get(suggestion_id))

    def edit(self, suggestion_id: int, changes: dict[str, Any]) -> dict:
        """US-15 CA01 (editar): altera o conteúdo proposto antes da decisão."""
        suggestion = self._get(suggestion_id)
        self._require_pending(suggestion)
        proposal = suggestion["conteudo_proposto"]
        merged = {**proposal, **build_content({**proposal, **changes})}
        self.uow.suggestions.update(suggestion_id, {
            "conteudo_proposto": merged, "editada": 1, "atualizado_em": utc_now_iso()
        })
        return self.get(suggestion_id)

    def approve(self, suggestion_id: int, author: str, changes: dict[str, Any] | None = None,
                accept_outdated: bool = False, accept_duplicate: bool = False) -> dict:
        """US-15 CA02/CA04, US-18 CA01: aplica a proposta aprovada.

        Confirmação: só vincula as evidências novas. Demais categorias: aplica o conteúdo proposto (se mudou, cria
        versão) e vincula as evidências. Nova necessidade: cria a história com código definitivo (US-…).

        Proposta desatualizada (a história mudou depois dela) → 409 ``proposta_desatualizada``; com
        ``accept_outdated``, aplica sobre a versão atual SÓ os campos que a proposta muda, preservando o que foi
        alterado depois. Nova necessidade parecida demais com uma história ativa → 409 ``possivel_duplicidade``,
        salvo ``accept_duplicate``."""
        if changes:
            self.edit(suggestion_id, changes)
        suggestion = self._get(suggestion_id)
        self._require_pending(suggestion)
        origin = ChangeOrigin.AI_SUGGESTION_EDITED if suggestion["editada"] else ChangeOrigin.AI_SUGGESTION
        proposal = suggestion["conteudo_proposto"]
        values: dict[str, Any] = {"status": SuggestionStatus.APPROVED.value}
        if suggestion["historia_id"]:
            story = self.uow.stories.get(suggestion["historia_id"])
            if not story:
                raise ConflictError("A história desta sugestão não existe mais.")
            changed = None
            if suggestion["tipo"] != SuggestionType.CONFIRMATION.value or suggestion["editada"]:
                fields = self._fields_to_apply(suggestion, story, accept_outdated)
                hints = {c["criterio_id"]: c["acao_recomendada"]
                         for c in (suggestion.get("detalhes") or {}).get("criterios_afetados", [])}
                changed = apply_content_change(self.uow, story, fields, criteria_hints=hints) if fields else None
            add_evidences(self.uow, story["id"], suggestion["evidencias"], "atualizacao")
            if changed:
                values["versao_aplicada"] = create_version(
                    self.uow, story["id"], ChangeType.AUTOMATIC, origin, author, suggestion_id,
                    note=suggestion["justificativa"],
                )
        else:
            self._check_duplicate(suggestion, accept_duplicate)
            values["historia_criada_id"] = create_story_record(
                self.uow, suggestion["projeto_id"], proposal, origin="ia", change_type=ChangeType.AUTOMATIC,
                change_origin=origin, author=author, evidence_specs=suggestion["evidencias"],
                evidence_origin="atualizacao", suggestion_id=suggestion_id,
            )
            values["versao_aplicada"] = 1
        now = utc_now_iso()
        self.uow.suggestions.update(suggestion_id, {**values, "decidido_por": author, "decidido_em": now, "atualizado_em": now})
        return self.get(suggestion_id)

    def _fields_to_apply(self, suggestion: dict, story: dict, accept_outdated: bool) -> dict[str, Any]:
        proposal = suggestion["conteudo_proposto"]
        if not is_outdated(suggestion, story):
            return {k: proposal.get(k) for k in EDITABLE_FIELDS}
        changed = proposal_changes(suggestion)
        if not accept_outdated:
            raise ConflictError(
                "A história mudou depois desta proposta. Revise a comparação com a versão atual e aprove de novo "
                "confirmando, ou cancele a proposta.",
                {"motivo": "proposta_desatualizada", "versao_base": suggestion.get("versao_base"),
                 "versao_atual": story["versao_atual"], "campos_propostos": list(changed),
                 "comparacao_atual": presenters.comparison(snapshot(story), {**snapshot(story), **changed})},
            )
        return changed

    def _check_duplicate(self, suggestion: dict, accept_duplicate: bool) -> None:
        """Consolidação antes do código definitivo: o backlog pode ter mudado desde a análise."""
        if accept_duplicate:
            return
        key = story_key(suggestion["conteudo_proposto"])
        for story in self.uow.stories.list_active(suggestion["projeto_id"]):
            score = similarity(f"{story['perfil'] or ''} {story['funcionalidade'] or ''}", key)
            if score >= EXISTING_DUPLICATE_THRESHOLD:
                raise ConflictError(
                    f"A história candidata repete {story['codigo']}. Confirme para criar mesmo assim, ou cancele.",
                    {"motivo": "possivel_duplicidade", "historia_id": story["id"], "codigo": story["codigo"],
                     "similaridade": round(score, 2)},
                )

    def _decide(self, suggestion_id: int, status: SuggestionStatus, author: str, reason: str | None) -> dict:
        suggestion = self._get(suggestion_id)
        self._require_pending(suggestion)
        now = utc_now_iso()
        self.uow.suggestions.update(suggestion_id, {
            "status": status.value, "decidido_por": author, "decidido_em": now, "motivo_decisao": reason,
            "atualizado_em": now,
        })
        return self.get(suggestion_id)

    def reject(self, suggestion_id: int, author: str, reason: str | None = None) -> dict:
        """US-15 CA03: o analista discorda da proposta; a história é preservada."""
        return self._decide(suggestion_id, SuggestionStatus.REJECTED, author, reason)

    def cancel(self, suggestion_id: int, author: str, reason: str | None = None) -> dict:
        """A proposta deixa de valer sem julgamento de mérito (ex.: desatualizada, fonte errada). Fica preservada."""
        return self._decide(suggestion_id, SuggestionStatus.CANCELLED, author, reason or "Cancelada pelo analista.")


def proposal_changes(suggestion: dict) -> dict[str, Any]:
    """Campos que a proposta muda em relação ao conteúdo da história quando ela foi gerada."""
    base, proposal = suggestion["conteudo_atual"] or {}, suggestion["conteudo_proposto"]
    changes = {k: proposal.get(k) for k in ("titulo", "perfil", "funcionalidade", "beneficio")
               if normalize(base.get(k)) != normalize(proposal.get(k))}
    if criteria_content(base.get("criterios_aceitacao")) != criteria_content(proposal.get("criterios_aceitacao")):
        changes["criterios_aceitacao"] = proposal.get("criterios_aceitacao")
    return changes


def is_outdated(suggestion: dict, story: dict) -> bool:
    """A história mudou depois da proposta: versão diferente da registrada ou conteúdo diferente do de então."""
    if suggestion.get("versao_base") is not None and suggestion["versao_base"] != story["versao_atual"]:
        return True
    return bool(suggestion["conteudo_atual"]) and not same_content(suggestion["conteudo_atual"], story)
