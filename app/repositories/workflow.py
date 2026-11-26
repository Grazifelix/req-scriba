"""Repositórios do fluxo iterativo: avaliações, perguntas, sugestões e jobs."""

from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository


class EvaluationRepository(BaseRepository):
    table = "avaliacoes_qualidade"
    json_columns = ("avaliacao_criterios", "analise_transversal_conjunto", "lacunas")

    def list_by_story(self, story_id: int) -> list[dict[str, Any]]:
        return self._select("historia_id = ?", (story_id,), order="id")

    def latest(self, story_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM avaliacoes_qualidade WHERE historia_id = ? ORDER BY id DESC LIMIT 1",
            (story_id,),
        ).fetchone()
        return self._decode(row)

    def latest_rewrites(self, project_id: int) -> dict[int, str | None]:
        """Reescrita sugerida da avaliação mais recente de cada história do projeto."""
        rows = self.conn.execute(
            "SELECT a.historia_id, a.reescrita_sugerida FROM avaliacoes_qualidade a "
            "JOIN historias h ON h.id = a.historia_id "
            "WHERE h.projeto_id = ? AND a.id = ("
            "SELECT MAX(id) FROM avaliacoes_qualidade WHERE historia_id = a.historia_id)",
            (project_id,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}


class QuestionRepository(BaseRepository):
    table = "perguntas_followup"

    def list_filtered(
        self,
        project_id: int,
        status: str | None = None,
        story_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["projeto_id = ?"]
        params: list[Any] = [project_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if statuses:
            clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if story_id:
            clauses.append("historia_id = ?")
            params.append(story_id)
        return self._select(" AND ".join(clauses), tuple(params))


class UpdateSuggestionRepository(BaseRepository):
    table = "sugestoes_atualizacao"
    json_columns = ("conteudo_atual", "conteudo_proposto", "evidencias", "fontes", "detalhes")

    def list_by_project(self, project_id: int, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self._select("projeto_id = ? AND status = ?", (project_id, status))
        return self._select("projeto_id = ?", (project_id,))

    def count_pending_for_story(self, story_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM sugestoes_atualizacao WHERE historia_id = ? AND status = 'pendente'",
            (story_id,),
        ).fetchone()
        return int(row[0])


class JobRepository(BaseRepository):
    table = "jobs"
    json_columns = ("parametros", "resultado")

    def list_by_project(self, project_id: int) -> list[dict[str, Any]]:
        return self._select("projeto_id = ?", (project_id,), order="criado_em DESC")

    def list_unfinished(self) -> list[dict[str, Any]]:
        return self._select("status IN ('pendente', 'processando')")

    def has_active(self, project_id: int, job_type: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM jobs WHERE projeto_id = ? AND tipo = ? AND status IN ('pendente', 'processando') LIMIT 1",
            (project_id, job_type),
        ).fetchone()
        return row is not None
