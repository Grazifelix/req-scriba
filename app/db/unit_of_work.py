"""Unit of Work: uma conexão/transação com todos os repositórios."""

from __future__ import annotations

from types import TracebackType

from app.db.database import Database
from app.repositories.projects import ProjectRepository
from app.repositories.sources import (
    AudioRepository,
    DocumentRepository,
    SegmentRepository,
    SourceChunkRepository,
)
from app.repositories.stories import EvidenceRepository, StoryRepository, VersionRepository
from app.repositories.workflow import (
    EvaluationRepository,
    JobRepository,
    QuestionRepository,
    UpdateSuggestionRepository,
)


class UnitOfWork:
    """Uso: ``with UnitOfWork(db) as uow: ...`` — commit no sucesso, rollback em exceção."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.conn = db.connect()
        self.projects = ProjectRepository(self.conn)
        self.audios = AudioRepository(self.conn)
        self.segments = SegmentRepository(self.conn)
        self.documents = DocumentRepository(self.conn)
        self.chunks = SourceChunkRepository(self.conn)
        self.stories = StoryRepository(self.conn)
        self.versions = VersionRepository(self.conn)
        self.evidences = EvidenceRepository(self.conn)
        self.evaluations = EvaluationRepository(self.conn)
        self.questions = QuestionRepository(self.conn)
        self.suggestions = UpdateSuggestionRepository(self.conn)
        self.jobs = JobRepository(self.conn)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "UnitOfWork":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
