"""Contrato dos provedores de LLM e utilitários comuns aos adaptadores HTTP."""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import requests

from app.core.errors import ExternalServiceError
from app.llm.parsing import extract_json

logger = logging.getLogger(__name__)

STRICT_JSON_REMINDER = (
    "Sua resposta anterior não era um JSON válido. Responda novamente APENAS com um objeto JSON "
    "válido, sem texto antes ou depois."
)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

ChatFunction = Callable[[list[dict[str, str]]], str]


class LLMClient(Protocol):
    def generate_json(self, system: str, prompt: str) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...


def generate_json_with_retry(chat: ChatFunction, system: str, prompt: str) -> dict[str, Any]:
    """Envia o prompt e devolve o objeto JSON da resposta (1 retentativa se inválido)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    content = chat(messages)
    parsed = extract_json(content)
    if parsed is not None:
        return parsed
    logger.warning("LLM retornou JSON inválido; nova tentativa com instrução estrita")
    messages += [{"role": "assistant", "content": content}, {"role": "user", "content": STRICT_JSON_REMINDER}]
    parsed = extract_json(chat(messages))
    if parsed is None:
        raise ExternalServiceError("O modelo de linguagem não retornou um JSON válido após nova tentativa.")
    return parsed


def post_json(
    session: requests.Session,
    url: str,
    payload: dict[str, Any],
    timeout: int,
    provider: str,
    secret: str | None = None,
) -> dict[str, Any]:
    """POST JSON que converte falhas em ExternalServiceError, sem expor `secret` nos detalhes."""
    try:
        response = session.post(url, json=payload, timeout=timeout)
    except requests.Timeout as exc:
        raise ExternalServiceError(f"Tempo esgotado ao chamar {provider} ({url}).") from exc
    except requests.RequestException as exc:
        raise ExternalServiceError(
            f"Não foi possível conectar a {provider} ({url}). Verifique se o serviço está em execução."
        ) from exc
    if response.status_code >= 400:
        detail = response.text
        if secret:
            detail = detail.replace(secret, "***")
        raise ExternalServiceError(
            f"{provider} respondeu com erro HTTP {response.status_code}.", {"resposta": detail[:300]}
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ExternalServiceError(f"Resposta de {provider} não é JSON.") from exc


def check_model(
    provider: str,
    model: str,
    list_models: Callable[[], list[str]],
    matches: Callable[[str, str], bool] = str.__eq__,
) -> dict[str, Any]:
    """Saúde de um modelo: o provedor responde e o modelo está na lista de `list_models`."""
    result: dict[str, Any] = {"provedor": provider, "modelo": model}
    try:
        models = list_models()
    except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
        return {**result, "status": "indisponivel", "detalhe": str(exc)[:200]}
    missing = [] if any(matches(listed, model) for listed in models) else [model]
    return {**result, "status": "modelos_ausentes" if missing else "ok", "modelos_ausentes": missing}


def is_local_url(url: str) -> bool:
    return urlparse(url).hostname in LOCAL_HOSTS
