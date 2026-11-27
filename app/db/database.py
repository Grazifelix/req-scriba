"""Acesso ao SQLite: criação de conexões e inicialização do schema."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

OLD_ITEM_TYPES = "'funcionalidade', 'fora_de_escopo'"
NEW_ITEM_TYPES = "'funcionalidade', 'termo', 'fora_de_escopo'"


NEW_COLUMNS = (
    ("evidencias", "afirmacoes_sustentadas", "TEXT NOT NULL DEFAULT '[]'"),
    ("versoes_historia", "criterio_ids", "TEXT NOT NULL DEFAULT '[]'"),
    ("sugestoes_atualizacao", "versao_base", "INTEGER"),
    ("sugestoes_atualizacao", "detalhes", "TEXT NOT NULL DEFAULT '{}'"),
    ("versoes_historia", "fontes", "TEXT NOT NULL DEFAULT '[]'"),
    ("versoes_historia", "status_historia", "TEXT"),
    ("sugestoes_atualizacao", "motivo_decisao", "TEXT"),
    ("criterios_aceitacao", "situacao", "TEXT NOT NULL DEFAULT 'valido'"),
    ("criterios_aceitacao", "validado_na_versao", "INTEGER"),
    ("criterios_aceitacao", "desatualizado_na_versao", "INTEGER"),
    ("criterios_aceitacao", "motivo_situacao", "TEXT"),
)
# CHECK de sugestoes_atualizacao.tipo: bancos anteriores ao Milestone 3 terminam em 'novo_requisito'), os do
# Milestone 3 em 'nova_necessidade'); ambos passam à lista atual.
OLD_SUGGESTION_TYPES = ("'complementa', 'contradiz', 'novo_requisito')", "'contradicao', 'nova_necessidade')")
NEW_SUGGESTION_TYPES = ("'complementa', 'contradiz', 'novo_requisito', 'confirmacao', 'complementacao', 'modificacao', "
                        "'contradicao', 'nova_necessidade', 'revisao_criterios')")
CRITERIA_MIGRATION = "criterios_tabela_v1"
CRITERION_EXTRAS = ("dado", "quando", "entao")


def _migrate(conn: sqlite3.Connection) -> None:
    """Ajustes de bancos criados por versões anteriores do schema (idempotente)."""
    _migrate_item_types(conn)
    for old in OLD_SUGGESTION_TYPES:
        _recreate_with_new_check(conn, "sugestoes_atualizacao", old, NEW_SUGGESTION_TYPES,
                                 "CREATE INDEX IF NOT EXISTS ix_sugestoes_projeto ON sugestoes_atualizacao(projeto_id, status)")
    for table, column, ddl in NEW_COLUMNS:
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    # critérios existentes valem para a versão em que entraram
    conn.execute("UPDATE criterios_aceitacao SET validado_na_versao = versao_historia WHERE validado_na_versao IS NULL")
    conn.commit()
    _migrate_criteria(conn)


def _migrate_criteria(conn: sqlite3.Connection) -> None:
    """Copia os critérios do JSON ``historias.criterios_aceitacao`` para a tabela ``criterios_aceitacao``
    (uma única vez, registrada em ``migracoes``). Cada critério vira uma linha vigente da versão atual da HU,
    e a versão atual passa a listar esses ids. A coluna antiga não é alterada (rollback possível)."""
    if conn.execute("SELECT 1 FROM migracoes WHERE nome = ?", (CRITERIA_MIGRATION,)).fetchone():
        return
    stories = conn.execute(
        "SELECT id, versao_atual, origem, criterios_aceitacao, atualizado_em FROM historias "
        "WHERE criterios_aceitacao NOT IN ('', '[]') AND id NOT IN (SELECT historia_id FROM criterios_aceitacao)"
    ).fetchall()
    try:
        for story_id, version, origin, raw, updated in stories:
            ids = [_insert_legacy_criterion(conn, story_id, version, position, item, updated)
                   for position, item in enumerate(_valid_criteria(raw), start=1)]
            conn.execute("UPDATE versoes_historia SET criterio_ids = ? WHERE historia_id = ? AND numero = ?",
                         (json.dumps(ids), story_id, version))
        conn.execute("INSERT INTO migracoes (nome, aplicada_em) VALUES (?, ?)",
                     (CRITERIA_MIGRATION, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _valid_criteria(raw: str) -> list[dict]:
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [c for c in items if isinstance(c, dict) and str(c.get("criterio") or "").strip()]


def _insert_legacy_criterion(conn: sqlite3.Connection, story_id: int, version: int, position: int, item: dict,
                             updated: str) -> int:
    origin = item.get("origem") if item.get("origem") in ("ia", "manual") else "legado"
    cur = conn.execute(
        "INSERT INTO criterios_aceitacao (historia_id, codigo, posicao, titulo, criterio, dado, quando, entao, fontes, "
        "informacoes_ausentes, afirmacoes_sem_evidencia, origem, versao_historia, status, criado_em, atualizado_em) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'vigente', ?, ?)",
        (story_id, f"CA{position:02d}", position, str(item.get("titulo") or f"Critério {position}").strip(),
         str(item["criterio"]).strip(), *(item.get(k) for k in CRITERION_EXTRAS),
         json.dumps(item.get("fontes") or [], ensure_ascii=False),
         json.dumps(item.get("informacoes_ausentes") or [], ensure_ascii=False),
         json.dumps(item.get("afirmacoes_sem_evidencia") or [], ensure_ascii=False),
         origin, version, updated, updated),
    )
    return int(cur.lastrowid)


def _recreate_with_new_check(conn: sqlite3.Connection, table: str, old: str, new: str, index_ddl: str) -> None:
    """Recria ``table`` trocando o trecho ``old`` do DDL (um CHECK) por ``new`` e copia os dados (idempotente)."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if not row or old not in row[0]:
        return
    ddl = row[0].replace(old, new).replace(table, f"{table}_nova", 1)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"""
            BEGIN;
            {ddl};
            INSERT INTO {table}_nova SELECT * FROM {table};
            DROP TABLE {table};
            ALTER TABLE {table}_nova RENAME TO {table};
            {index_ddl};
            COMMIT;
        """)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_item_types(conn: sqlite3.Connection) -> None:
    """`CREATE TABLE IF NOT EXISTS` não altera tabelas existentes e o SQLite não altera CHECK com ALTER TABLE:
    a tabela é recriada com o DDL novo e os dados copiados (procedimento recomendado pelo SQLite)."""
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
