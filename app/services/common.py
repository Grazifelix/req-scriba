"""Buscas com verificação de existência, compartilhadas pelos serviços."""

from __future__ import annotations

from app.core.errors import NotFoundError
from app.db.unit_of_work import UnitOfWork


def require_project(uow: UnitOfWork, project_id: int) -> dict:
    project = uow.projects.get(project_id)
    if not project:
        raise NotFoundError.for_entity("Projeto", project_id)
    return project


def require_story(uow: UnitOfWork, story_id: int) -> dict:
    story = uow.stories.get(story_id)
    if not story:
        raise NotFoundError.for_entity("História", story_id)
    return story


def require_audio(uow: UnitOfWork, audio_id: int) -> dict:
    audio = uow.audios.get(audio_id)
    if not audio:
        raise NotFoundError.for_entity("Áudio", audio_id)
    return audio


def require_document(uow: UnitOfWork, document_id: int) -> dict:
    document = uow.documents.get(document_id)
    if not document:
        raise NotFoundError.for_entity("Documento", document_id)
    return document
