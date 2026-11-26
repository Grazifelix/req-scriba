"""Repositórios de histórias, versões e evidências."""

from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository, placeholders


LEGACY_CRITERIA_COLUMN = "criterios_aceitacao"


def criterion_item(row: dict[str, Any]) -> dict[str, Any]:
    """Linha de ``criterios_aceitacao`` no formato da lista de critérios da história (API e snapshots)."""
    item = {"id": row["codigo"], "criterio_id": row["id"], "titulo": row["titulo"], "criterio": row["criterio"]}
    item.update({k: row[k] for k in ("dado", "quando", "entao") if row[k] is not None})
    item.update({k: row[k] or [] for k in ("fontes", "informacoes_ausentes", "afirmacoes_sem_evidencia")})
    item.update({"origem": row["origem"], "versao_historia": row["versao_historia"], "status": row["status"]})
    return item


class CriterionRepository(BaseRepository):
    table = "criterios_aceitacao"
    json_columns = ("fontes", "informacoes_ausentes", "afirmacoes_sem_evidencia")

    def list_current(self, story_id: int) -> list[dict[str, Any]]:
        return self._select("historia_id = ? AND status = 'vigente'", (story_id,), order="posicao, id")

    def list_by_story(self, story_id: int) -> list[dict[str, Any]]:
        """Todos os critérios, inclusive os substituídos (histórico preservado)."""
        return self._select("historia_id = ?", (story_id,), order="id")

    def list_current_for(self, story_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        result: dict[int, list[dict[str, Any]]] = {i: [] for i in story_ids}
        if not story_ids:
            return result
        rows = self._select(f"status = 'vigente' AND historia_id IN ({placeholders(len(story_ids))})",
                            tuple(story_ids), order="historia_id, posicao, id")
        for row in rows:
            result[row["historia_id"]].append(row)
        return result


class StoryRepository(BaseRepository):
    """Histórias com ``criterios_aceitacao`` montado a partir da tabela de critérios (somente os vigentes).

    Os critérios são gravados só por ``services.criteria_store``: insert/update com a coluna antiga falham,
    para nada voltar a escrever no JSON legado sem querer."""

    table = "historias"
    json_columns = ("informacoes_ausentes", "afirmacoes_sem_evidencia")

    def _with_criteria(self, stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        current = CriterionRepository(self.conn).list_current_for([s["id"] for s in stories])
        for story in stories:
            story[LEGACY_CRITERIA_COLUMN] = [criterion_item(r) for r in current[story["id"]]]
        return stories

    def _guard(self, values: dict[str, Any]) -> None:
        if LEGACY_CRITERIA_COLUMN in values:
            raise ValueError("Critérios são gravados em criterios_aceitacao (services.criteria_store), não na história.")

    def insert(self, values: dict[str, Any]) -> int:
        self._guard(values)
        return super().insert(values)

    def update(self, row_id: Any, values: dict[str, Any]) -> None:
        self._guard(values)
        super().update(row_id, values)

    def get(self, row_id: Any) -> dict[str, Any] | None:
        story = super().get(row_id)
        return self._with_criteria([story])[0] if story else None

    def _select(self, where: str = "", params: tuple = (), order: str = "id") -> list[dict[str, Any]]:
        return self._with_criteria(super()._select(where, params, order))

    def list_by_project(self, project_id: int, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self._select("projeto_id = ? AND status = ?", (project_id, status))
        return self._select("projeto_id = ?", (project_id,))

    def list_active(self, project_id: int) -> list[dict[str, Any]]:
        """Histórias que compõem o backlog (todas exceto desaprovadas)."""
        return self._select("projeto_id = ? AND status != 'desaprovada'", (project_id,))

    def next_code(self, project_id: int) -> str:
        row = self.conn.execute(
            "SELECT MAX(CAST(SUBSTR(codigo, 4) AS INTEGER)) FROM historias WHERE projeto_id = ?",
            (project_id,),
        ).fetchone()
        return f"US-{(row[0] or 0) + 1:03d}"


class VersionRepository(BaseRepository):
    table = "versoes_historia"
    json_columns = ("conteudo", "evidencia_ids", "criterio_ids")

    def list_by_story(self, story_id: int) -> list[dict[str, Any]]:
        return self._select("historia_id = ?", (story_id,), order="numero")

    def get_by_number(self, story_id: int, number: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM versoes_historia WHERE historia_id = ? AND numero = ?", (story_id, number)
        ).fetchone()
        return self._decode(row)


class EvidenceRepository(BaseRepository):
    table = "evidencias"
    json_columns = ("localizacao", "afirmacoes_sustentadas")

    def list_by_story(self, story_id: int) -> list[dict[str, Any]]:
        return self._select("historia_id = ?", (story_id,))

    def list_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self._select(f"id IN ({placeholders(len(ids))})", tuple(ids))


class EvidenceAuditRepository(BaseRepository):
    table = "auditorias_evidencia"
    json_columns = ("componentes", "criterios", "problemas_conjunto", "resumo", "revisoes")

    def list_by_story(self, story_id: int) -> list[dict[str, Any]]:
        return self._select("historia_id = ?", (story_id,), order="id DESC")

    def latest(self, story_id: int) -> dict[str, Any] | None:
        rows = self.list_by_story(story_id)
        return rows[0] if rows else None
