"""Casos de uso de projetos (inclui campos derivados e qualidade geral do backlog)."""

from __future__ import annotations

from app.core.container import Container
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain import qus
from app.domain.enums import StoryStatus
from app.integrations.vector_store import build_where
from app.services import presenters
from app.services.common import require_project


def overall_quality(stories: list[dict]) -> dict:
    """RN06: média das porcentagens das HU avaliadas e não desaprovadas."""
    scores = [
        s["qualidade_porcentagem"] for s in stories
        if s["qualidade_porcentagem"] is not None and s["status"] != StoryStatus.REJECTED.value
    ]
    if not scores:
        return {"porcentagem": None, "status": qus.NOT_EVALUATED, "historias_avaliadas": 0}
    average = round(sum(scores) / len(scores), 2)
    return {"porcentagem": average, "status": qus.quality_label(average), "historias_avaliadas": len(scores)}


class ProjectService:
    def __init__(self, uow: UnitOfWork, container: Container) -> None:
        self.uow = uow
        self.container = container

    def _present(self, project: dict) -> dict:
        stories = self.uow.stories.list_by_project(project["id"])
        backlog = [s["id"] for s in stories if s["status"] != StoryStatus.REJECTED.value]
        approved = [s["id"] for s in stories if s["status"] == StoryStatus.APPROVED.value]
        return presenters.project(
            project, self.uow.projects.counts(project["id"]), overall_quality(stories), backlog, approved
        )

    def create(self, titulo: str, descricao: str | None) -> dict:
        now = utc_now_iso()
        project_id = self.uow.projects.insert(
            {"titulo": titulo, "descricao": descricao, "criado_em": now, "atualizado_em": now}
        )
        return self.get(project_id)

    def list(self) -> dict:
        return presenters.listing([self._present(p) for p in self.uow.projects.list_all()])

    def get(self, project_id: int) -> dict:
        return self._present(require_project(self.uow, project_id))

    def update(self, project_id: int, changes: dict) -> dict:
        require_project(self.uow, project_id)
        if changes:
            self.uow.projects.update(project_id, {**changes, "atualizado_em": utc_now_iso()})
        return self.get(project_id)

    def delete(self, project_id: int) -> None:
        require_project(self.uow, project_id)
        paths = [a["caminho_arquivo"] for a in self.uow.audios.list_by_project(project_id)]
        paths += [d["caminho_arquivo"] for d in self.uow.documents.list_by_project(project_id)]
        self.uow.projects.delete(project_id)  # cascade remove fontes, histórias, etc.
        self.container.vector_store.delete_where(build_where(project_id))
        for path in paths:
            self.container.storage.remove(path)
