"""Acesso ao SQLite: criação de conexões e inicialização do schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

OLD_ITEM_TYPES = "'funcionalidade', 'fora_de_escopo'"
NEW_ITEM_TYPES = "'funcionalidade', 'termo', 'fora_de_escopo'"


def _migrate(conn: sqlite3.Connection) -> None:
    """Ajustes de bancos criados por versões anteriores do schema (idempotente).

    `CREATE TABLE IF NOT EXISTS` não altera tabelas existentes e o SQLite não altera CHECK com ALTER TABLE:
    a tabela é recriada com o DDL novo e os dados copiados (procedimento recomendado pelo SQLite).
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'itens_analise'").fetchone()
    if not row or "'termo'" in row[0]:
        return
    ddl = row[0].replace(OLD_ITEM_TYPES, NEW_ITEM_TYPES).replace("itens_analise", "itens_analise_nova", 1)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"""
            BEGIN;
            {ddl};
            INSERT INTO itens_analise_nova SELECT * FROM itens_analise;
            DROP TABLE itens_analise;
            ALTER TABLE itens_analise_nova RENAME TO itens_analise;
            CREATE INDEX IF NOT EXISTS ix_itens_analise ON itens_analise(analise_id, posicao);
            COMMIT;
        """)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


class Database:
    """Fábrica de conexões SQLite configuradas para o ReqScriba."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.commit()
            _migrate(conn)
        finally:
            conn.close()

    def ping(self) -> bool:
        conn = self.connect()
        try:
            conn.execute("SELECT 1").fetchone()
            return True
        finally:
            conn.close()
