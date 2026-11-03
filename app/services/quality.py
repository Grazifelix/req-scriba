"""Avaliação de qualidade QUS, defeitos, comparação e visão geral (US-07, US-08, US-09)."""

from __future__ import annotations

from app.core.container import Container
from app.core.errors import ConflictError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain import qus, story_format
from app.domain.enums import StoryStatus, label_of
from app.domain.text import similarity
from app.llm import parsing, prompts
from app.services import presenters
from app.services.common import require_project, require_story
from app.services.projects import overall_quality

UNIQUE_THRESHOLD = 0.90
MAX_BACKLOG_CONTEXT = 30


def _deterministic_individual(story: dict, criteria: dict) -> None:
    """well_formed é verificável: sem perfil ou funcionalidade → Não atende (sobrepõe o LLM)."""
    missing = [e for e in ("perfil", "funcionalidade") if not story.get(e)]
    if missing:
        criteria["well_formed"] = {
            "resultado": qus.FAILS,
            "evidencia": f"A história não possui: {', '.join(missing)}.",
            "acao": "Inclua o perfil de usuário e a funcionalidade desejada.",
        }


def _deterministic_set(story: dict, others: list[dict]) -> dict:
    result = {}
    duplicate = max(others, key=lambda o: similarity(o["historia"], story["historia"]), default=None)
    score = similarity(duplicate["historia"], story["historia"]) if duplicate else 0.0
    result["unique"] = (
        {"resultado": qus.FAILS, "observacao": f"Muito similar a {duplicate['codigo']} (similaridade {score:.2f})."}
        if duplicate and score >= UNIQUE_THRESHOLD
        else {"resultado": qus.MEETS, "observacao": "Não há história duplicada no backlog."}
    )
    follows = story_format.follows_template(story["perfil"], story["funcionalidade"], story["beneficio"])
    result["uniform"] = (
        {"resultado": qus.MEETS, "observacao": "Segue o template padrão 'Como…, quero…, para…' do backlog."}
        if follows
        else {"resultado": qus.ATTENTION, "observacao": "Não segue o template completo: há elemento [A DEFINIR]."}
    )
    if not others:
        alone = "Backlog contém apenas esta história."
        result["conflict_free"] = {"resultado": qus.MEETS, "observacao": alone}
        result["independent"] = {"resultado": qus.MEETS, "observacao": alone}
    return result


def evaluation_defects(evaluation: dict | None) -> list[dict]:
    if not evaluation:
        return []
    defects = []
    for group, text_key in (("avaliacao_criterios", "evidencia"), ("analise_transversal_conjunto", "observacao")):
        for key, entry in evaluation[group].items():
            if entry["resultado"] != qus.MEETS:
                defects.append({
                    "chave": f"qus:{key}", "tipo": key, "criterio": key, "resultado": entry["resultado"],
                    "descricao": f"{qus.criterion_description(key)} — {entry.get(text_key) or ''}".strip(" —"),
                    "acao_sugerida": entry.get("acao"),
                })
    return defects


def story_defects(story: dict, evaluation: dict | None) -> list[dict]:
    """Defeitos atuais: violações QUS + lacunas + afirmações sem evidência (US-09)."""
    defects = evaluation_defects(evaluation)
    gaps = list(story["informacoes_ausentes"])
    for gap in (evaluation or {}).get("lacunas", []):
        if all(similarity(gap["descricao"], g["descricao"]) < 0.85 for g in gaps):
            gaps.append(gap)
    for i, gap in enumerate(gaps):
        defects.append({
            "chave": f"lacuna:{i}", "tipo": "lacuna", "criterio": None, "resultado": None,
            "descricao": f"Informação ausente ({gap['elemento']}): {gap['descricao']}",
            "acao_sugerida": "Esclarecer com os stakeholders; não preencher por suposição.",
        })
    for i, claim in enumerate(story["afirmacoes_sem_evidencia"]):
        defects.append({
            "chave": f"sem_evidencia:{i}", "tipo": "sem_evidencia", "criterio": None, "resultado": None,
            "descricao": f"Afirmação sem suporte nas fontes: {claim}",
            "acao_sugerida": "Confirmar com os stakeholders ou remover a afirmação.",
        })
    return defects


class QualityService:
    def __init__(self, uow: UnitOfWork, container: Container) -> None:
        self.uow = uow
        self.container = container

    def evaluate(self, story_id: int) -> dict:
        """US-07 / US-08 CA01: avalia a versão atual e preserva o histórico."""
        story = require_story(self.uow, story_id)
        others = [s for s in self.uow.stories.list_active(story["projeto_id"]) if s["id"] != story_id]
        others.sort(key=lambda o: similarity(o["historia"], story["historia"]), reverse=True)
        context = others[:MAX_BACKLOG_CONTEXT]
        llm = self.container.llm

        individual = parsing.normalize_individual_evaluation(
            llm.generate_json(prompts.SYSTEM, prompts.individual_evaluation_prompt(story))
        )
        _deterministic_individual(story, individual["avaliacao_criterios"])

        set_result = _deterministic_set(story, context)
        llm_keys = [k for k in qus.SET_CRITERIA if k not in set_result]
        if llm_keys:
            raw = llm.generate_json(prompts.SYSTEM, prompts.set_evaluation_prompt(story, context, llm_keys))
            set_result.update(parsing.normalize_set_analysis(raw, llm_keys))
        set_result = {k: set_result[k] for k in qus.SET_CRITERIA}

        met = qus.count_met(individual["avaliacao_criterios"], set_result)
        score = qus.percentage(met)
        evaluation_id = self.uow.evaluations.insert({
            "historia_id": story_id, "versao_historia": story["versao_atual"], "porcentagem": score,
            "status": qus.quality_label(score), "criterios_atendidos": met,
            "avaliacao_criterios": individual["avaliacao_criterios"], "analise_transversal_conjunto": set_result,
            "lacunas": individual["lacunas"], "reescrita_sugerida": individual["reescrita_sugerida"],
            "criado_em": utc_now_iso(),
        })
        self.uow.stories.update(story_id, {
            "qualidade_porcentagem": score, "qualidade_status": qus.quality_label(score),
            "qualidade_avaliacao_id": evaluation_id,
            "sugestao_scriba": individual["reescrita_sugerida"] or story["sugestao_scriba"],
        })
        return presenters.evaluation(self.uow.evaluations.get(evaluation_id))

    def history(self, story_id: int) -> dict:
        require_story(self.uow, story_id)
        evaluations = [presenters.evaluation(e) for e in self.uow.evaluations.list_by_story(story_id)]
        return {"historia_id": story_id, **presenters.listing(evaluations)}

    def compare(self, story_id: int, from_id: int | None = None, to_id: int | None = None) -> dict:
        """US-08 CA02: indica defeitos resolvidos/novos sem apagar registros anteriores."""
        require_story(self.uow, story_id)
        evaluations = self.uow.evaluations.list_by_story(story_id)
        by_id = {e["id"]: e for e in evaluations}
        if from_id is None and to_id is None:
            if len(evaluations) < 2:
                raise ConflictError("São necessárias ao menos duas avaliações para comparar.")
            before, after = evaluations[-2], evaluations[-1]
        else:
            if from_id not in by_id or to_id not in by_id:
                raise NotFoundError("Avaliação informada não pertence a esta história.")
            before, after = by_id[from_id], by_id[to_id]
        changes = []
        for group in ("avaliacao_criterios", "analise_transversal_conjunto"):
            for key in after[group]:
                old, new = before[group].get(key, {}).get("resultado"), after[group][key]["resultado"]
                if old != qus.MEETS and new == qus.MEETS:
                    change = "resolvido"
                elif old == qus.MEETS and new != qus.MEETS:
                    change = "novo_defeito"
                else:
                    change = "mantido_atende" if new == qus.MEETS else "mantido_com_problema"
                changes.append({"criterio": key, "antes": old, "depois": new, "mudanca": change})
        return {
            "historia_id": story_id,
            "de": {"avaliacao_id": before["id"], "versao_historia": before["versao_historia"], "porcentagem": before["porcentagem"]},
            "para": {"avaliacao_id": after["id"], "versao_historia": after["versao_historia"], "porcentagem": after["porcentagem"]},
            "variacao_porcentagem": round(after["porcentagem"] - before["porcentagem"], 2),
            "criterios": changes,
            "resolvidos": [c["criterio"] for c in changes if c["mudanca"] == "resolvido"],
            "novos_defeitos": [c["criterio"] for c in changes if c["mudanca"] == "novo_defeito"],
        }

    def defects(self, story_id: int) -> dict:
        story = require_story(self.uow, story_id)
        evaluation = self.uow.evaluations.latest(story_id)
        return {
            "historia_id": story_id, "avaliacao_id": evaluation["id"] if evaluation else None,
            "avaliacao_desatualizada": bool(evaluation and evaluation["versao_historia"] != story["versao_atual"]),
            **presenters.listing(story_defects(story, evaluation)),
        }

    def project_overview(self, project_id: int) -> dict:
        """US-07 CA03: identifica quais histórias têm problemas de qualidade."""
        require_project(self.uow, project_id)
        stories = self.uow.stories.list_by_project(project_id)
        distribution = {label: 0 for _, label in qus.QUALITY_BANDS}
        distribution.update({qus.EXCELLENT: 0, qus.NOT_EVALUATED: 0})
        items = []
        for s in stories:
            if s["status"] == StoryStatus.REJECTED.value:
                continue
            evaluation = self.uow.evaluations.latest(s["id"])
            label = qus.quality_label(s["qualidade_porcentagem"])
            distribution[label] += 1
            violated = [d["criterio"] for d in evaluation_defects(evaluation)]
            items.append({
                "historia_id": s["id"], "codigo": s["codigo"], "titulo": s["titulo"], "status": s["status"],
                "status_rotulo": label_of(StoryStatus, s["status"]), "porcentagem": s["qualidade_porcentagem"],
                "status_qualidade": label, "criterios_violados": violated,
                "possui_problemas": bool(violated or s["informacoes_ausentes"] or s["afirmacoes_sem_evidencia"]),
                "avaliada": evaluation is not None,
                "avaliacao_desatualizada": bool(evaluation and evaluation["versao_historia"] != s["versao_atual"]),
            })
        items.sort(key=lambda i: (i["porcentagem"] is not None, i["porcentagem"] or 0))
        return {
            "projeto_id": project_id, "qualidade_geral": overall_quality(stories), "distribuicao": distribution,
            "historias_com_problemas": sum(1 for i in items if i["possui_problemas"]),
            **presenters.listing(items),
        }
