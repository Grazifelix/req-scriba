"""Aplicação FastAPI do ReqScriba."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routers import health, projects, sources, stories, workflow
from app.core.config import Settings
from app.core.container import Container, build_container
from app.core.errors import AppError

logger = logging.getLogger("app")

API_PREFIX = "/api/v1"
DESCRIPTION = """
API do **ReqScriba**: elicitação apoiada por LLM com humano no controle.

Fluxo: projeto → áudios/documentos → transcrição e indexação → geração de Histórias de Usuário com
evidências → avaliação QUS → perguntas de follow-up → novas fontes → sugestões de atualização → versões.

Todas as respostas são JSON. Erros seguem `{"erro": {"codigo", "mensagem", "detalhes"}}`.
Envie o cabeçalho opcional `X-Usuario` para identificar o responsável pelas alterações.
"""

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _error(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"erro": {"codigo": code, "mensagem": message, "detalhes": details}})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"campo": ".".join(str(p) for p in err.get("loc", []) if p != "body"), "mensagem": err.get("msg")}
            for err in exc.errors()
        ]
        return _error(422, "entrada_invalida", "Dados de entrada inválidos.", fields)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = "Recurso não encontrado." if exc.status_code == 404 else str(exc.detail)
        return _error(exc.status_code, f"http_{exc.status_code}", message)

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Erro não tratado: %s", type(exc).__name__)
        return _error(500, "erro_interno", "Erro interno inesperado. Consulte os logs do servidor.")


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    settings = container.settings if container else (settings or Settings())
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not hasattr(app.state, "container"):
            app.state.container = build_container(settings)
        yield

    app = FastAPI(
        title="ReqScriba API", version=__version__, description=DESCRIPTION, lifespan=lifespan,
        openapi_tags=[
            {"name": "Saúde"}, {"name": "Projetos"}, {"name": "Áudios"}, {"name": "Transcrição"},
            {"name": "Documentos"}, {"name": "Busca"}, {"name": "Histórias de Usuário"},
            {"name": "Rastreabilidade"}, {"name": "Versões"}, {"name": "Qualidade"}, {"name": "Follow-up"},
            {"name": "Atualizações"}, {"name": "Jobs"},
        ],
    )
    if container is not None:
        app.state.container = container

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    register_error_handlers(app)
    for module in (health, projects, sources, stories, workflow):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"nome": "ReqScriba API", "versao": __version__, "documentacao": "/docs", "api": API_PREFIX}

    return app


app = create_app()
