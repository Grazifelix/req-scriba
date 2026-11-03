"""Perguntas de follow-up: geração, revisão, organização e respostas (US-10, US-11, US-12)."""

from __future__ import annotations

from app.core.container import Container
from app.core.errors import BusinessValidationError, ConflictError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import QuestionStatus, SourceType
from app.domain.qus import criterion_description
from app.llm import parsing, prompts
from app.services import presenters
from app.services.common import require_project, require_story
from app.services.quality import story_defects
from app.services.retrieval import describe_chunk

MAX_EVIDENCE_BLOCKS = 6
DEFECT_TYPE_LABELS = {"lacuna": "Lacuna de informação", "sem_evidencia": "Afirmação sem evidência"}


def defect_type_label(defect_type: str) -> str:
    return DEFECT_TYPE_LABELS.get(defect_type) or criterion_description(defect_type).split(":")[0]


class QuestionService:
    def __init__(self, uow: UnitOfWork, container: Container) -> None:
        self.uow = uow
        self.container = container

    def _get(self, question_id: int) -> dict:
        question = self.uow.questions.get(question_id)
        if not question:
            raise NotFoundError.for_entity("Pergunta", question_id)
        return question

    def _present(self, question: dict) -> dict:
        return presenters.question(question, self.uow.stories.get(question["historia_id"]))

    def generate(self, story_id: int, defect_keys: list[str] | None = None) -> dict:
        """US-10: ao menos uma pergunta por defeito com contexto; limitações explícitas (CA04)."""
        story = require_story(self.uow, story_id)
        evaluation = self.uow.evaluations.latest(story_id)
        defects = story_defects(story, evaluation)
        if defect_keys:
            unknown = set(defect_keys) - {d["chave"] for d in defects}
            if unknown:
                raise BusinessValidationError("Defeitos não encontrados na história.", {"defeitos": sorted(unknown)})
            defects = [d for d in defects if d["chave"] in defect_keys]
        if not defects:
            return {"historia_id": story_id, "perguntas": [], "total": 0, "limitacoes": [{
                "defeito_chave": None, "motivo": "Nenhum defeito identificado. Avalie a história (QUS) antes de gerar perguntas.",
            }]}
        labeled = [{**d, "rotulo": f"D{i}"} for i, d in enumerate(defects, start=1)]
        by_label = {d["rotulo"]: d for d in labeled}
        evidences = self.uow.evidences.list_by_story(story_id)[:MAX_EVIDENCE_BLOCKS]
        blocks = [
            {"rotulo": f"E{i}", "descricao": describe_chunk({**e, "texto": e["trecho"]}), "texto": e["trecho"]}
            for i, e in enumerate(evidences, start=1)
        ]
        data = self.container.llm.generate_json(prompts.SYSTEM, prompts.questions_prompt(story, labeled, blocks))
        questions, limitations = parsing.normalize_questions(data, set(by_label))

        now = utc_now_iso()
        created, covered = [], set()
        for q in questions:
            defect = by_label[q["defeito"]]
            covered.add(q["defeito"])
            created.append(self.uow.questions.insert({
                "projeto_id": story["projeto_id"], "historia_id": story_id,
                "avaliacao_id": evaluation["id"] if evaluation else None, "defeito_chave": defect["chave"],
                "tipo_defeito": defect["tipo"], "descricao_defeito": defect["descricao"], "pergunta": q["pergunta"],
                "justificativa": q["justificativa"], "status": QuestionStatus.PENDING.value, "editada": 0,
                "criado_em": now, "atualizado_em": now,
            }))
        reported = [
            {"defeito_chave": by_label[lim["defeito"]]["chave"] if lim["defeito"] else None, "motivo": lim["motivo"]}
            for lim in limitations
        ]
        reported_labels = {lim["defeito"] for lim in limitations}
        for label, defect in by_label.items():
            if label not in covered and label not in reported_labels:
                reported.append({
                    "defeito_chave": defect["chave"],
                    "motivo": "Não há contexto suficiente nas fontes para formular uma pergunta específica.",
                })
        items = [self._present(self.uow.questions.get(qid)) for qid in created]
        return {"historia_id": story_id, "perguntas": items, "total": len(items), "limitacoes": reported}

    def list(self, project_id: int, status: str | None = None, story_id: int | None = None) -> dict:
        require_project(self.uow, project_id)
        questions = self.uow.questions.list_filtered(project_id, status=status, story_id=story_id)
        return presenters.listing([self._present(q) for q in questions])

    def followup(self, project_id: int, group_by: str) -> dict:
        """US-12 CA01/CA02: perguntas aprovadas/respondidas agrupadas por história ou tipo de defeito."""
        require_project(self.uow, project_id)
        questions = self.uow.questions.list_filtered(
            project_id, statuses=(QuestionStatus.APPROVED.value, QuestionStatus.ANSWERED.value)
        )
        groups: dict[str, dict] = {}
        for q in questions:
            presented = self._present(q)
            if group_by == "historia":
                key = str(q["historia_id"])
                group = groups.setdefault(key, {
                    "historia_id": q["historia_id"], "codigo": presented["historia_codigo"],
                    "titulo": presented["historia_titulo"], "perguntas": [],
                })
            else:
                key = q["tipo_defeito"]
                group = groups.setdefault(key, {
                    "tipo_defeito": key, "rotulo": defect_type_label(key), "perguntas": [],
                })
            group["perguntas"].append(presented)
        return {
            "projeto_id": project_id, "agrupar_por": group_by, "grupos": list(groups.values()),
            "total_grupos": len(groups), "total_perguntas": len(questions),
            "pendentes_de_resposta": sum(1 for q in questions if q["status"] == QuestionStatus.APPROVED.value),
        }

    def edit(self, question_id: int, text: str) -> dict:
        """US-11 CA01."""
        question = self._get(question_id)
        if question["status"] == QuestionStatus.ANSWERED.value:
            raise ConflictError("Perguntas já respondidas não podem ser editadas.")
        self.uow.questions.update(question_id, {"pergunta": text, "editada": 1, "atualizado_em": utc_now_iso()})
        return self._present(self._get(question_id))

    def set_status(self, question_id: int, status: QuestionStatus) -> dict:
        """US-11 CA02/CA03: aprovar (disponível para próxima rodada) ou rejeitar."""
        question = self._get(question_id)
        if question["status"] == QuestionStatus.ANSWERED.value:
            raise ConflictError("Perguntas já respondidas não podem mudar de status.")
        self.uow.questions.update(question_id, {"status": status.value, "atualizado_em": utc_now_iso()})
        return self._present(self._get(question_id))

    def answer(self, question_id: int, answer: str, source_type: str | None, source_id: int | None) -> dict:
        """US-12 CA03: resposta associada à pergunta e, por ela, à história."""
        question = self._get(question_id)
        if question["status"] not in (QuestionStatus.APPROVED.value, QuestionStatus.ANSWERED.value):
            raise ConflictError("Apenas perguntas aprovadas podem ser respondidas.")
        if (source_type is None) != (source_id is None):
            raise BusinessValidationError("Informe 'resposta_tipo_fonte' e 'resposta_fonte_id' juntos.")
        if source_type:
            repo = self.uow.audios if source_type == SourceType.AUDIO.value else self.uow.documents
            source = repo.get(source_id)
            if not source or source["projeto_id"] != question["projeto_id"]:
                raise BusinessValidationError("A fonte da resposta não pertence a este projeto.")
        now = utc_now_iso()
        self.uow.questions.update(question_id, {
            "resposta": answer, "resposta_tipo_fonte": source_type, "resposta_fonte_id": source_id,
            "status": QuestionStatus.ANSWERED.value, "respondida_em": now, "atualizado_em": now,
        })
        return self._present(self._get(question_id))
