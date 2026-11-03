"""Dependências injetadas nos routers."""

from __future__ import annotations

from typing import Iterator, Optional

from fastapi import Depends, Header, Request

from app.core.container import Container
from app.db.unit_of_work import UnitOfWork

DEFAULT_ACTOR = "engenheiro"


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_uow(container: Container = Depends(get_container)) -> Iterator[UnitOfWork]:
    """Uma transação por requisição: commit no sucesso, rollback em erro."""
    uow = container.uow()
    try:
        yield uow
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.close()


def get_actor(x_usuario: Optional[str] = Header(default=None, max_length=100)) -> str:
    """Responsável pelas alterações. Ponto único a trocar quando houver autenticação."""
    actor = (x_usuario or "").strip()
    return actor or DEFAULT_ACTOR
