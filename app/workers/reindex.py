"""Reconstrói o índice vetorial com o modelo de embeddings configurado (`make reindex`).

Pare a API antes de executar: o ChromaDB local não é feito para escrita por dois processos.
"""

from __future__ import annotations

import logging
import sys

from app.core.config import Settings
from app.core.container import build_container
from app.core.errors import ExternalServiceError
from app.services.indexing import reindex_all

logger = logging.getLogger(__name__)


def main() -> int:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        total = reindex_all(build_container(settings))
    except ExternalServiceError as exc:
        logger.error("Reindexação interrompida: %s Execute novamente após corrigir.", exc.message)
        return 1
    logger.info("Reindexação concluída: %d trechos.", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
