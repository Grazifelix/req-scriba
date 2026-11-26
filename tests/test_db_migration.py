"""Migração de bancos criados antes do tipo de item "termo" (CHECK de itens_analise)."""

import sqlite3

import pytest

from app.db.database import Database

OLD_DDL = """
CREATE TABLE projetos (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT NOT NULL);
CREATE TABLE analises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE
);
CREATE TABLE itens_analise (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analise_id INTEGER NOT NULL REFERENCES analises(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL CHECK (tipo IN ('participante', 'usuario', 'requisito_negocio', 'funcionalidade', 'fora_de_escopo')),
    codigo TEXT,
    texto TEXT NOT NULL,
    complemento TEXT,
    referencias TEXT NOT NULL DEFAULT '[]',
    evidencias TEXT NOT NULL DEFAULT '[]',
    historia_ids TEXT NOT NULL DEFAULT '[]',
    origem TEXT NOT NULL CHECK (origem IN ('ia', 'manual')),
    editado INTEGER NOT NULL DEFAULT 0,
    posicao INTEGER NOT NULL,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
INSERT INTO projetos (titulo) VALUES ('Escola');
INSERT INTO analises (projeto_id) VALUES (1);
INSERT INTO itens_analise (analise_id, tipo, codigo, texto, origem, posicao, criado_em, atualizado_em)
VALUES (1, 'funcionalidade', 'FN1', 'emitir o boletim', 'ia', 0, 'x', 'x');
"""


def insert_term(conn):
    conn.execute(
        "INSERT INTO itens_analise (analise_id, tipo, texto, complemento, origem, posicao, criado_em, atualizado_em) "
        "VALUES (1, 'termo', 'OS', 'ordem de serviço', 'manual', 1, 'x', 'x')"
    )


def test_old_database_accepts_term_after_initialize(tmp_path):
    path = tmp_path / "antigo.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_DDL)
    with pytest.raises(sqlite3.IntegrityError):
        insert_term(conn)
    conn.close()

    database = Database(path)
    database.initialize()
    database.initialize()  # idempotente

    conn = database.connect()
    try:
        [old] = conn.execute("SELECT tipo, codigo, texto FROM itens_analise").fetchall()
        assert tuple(old) == ("funcionalidade", "FN1", "emitir o boletim")  # dados preservados
        insert_term(conn)
        conn.commit()
        assert conn.execute("SELECT count(*) FROM itens_analise WHERE tipo = 'termo'").fetchone()[0] == 1
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(itens_analise)")}
        assert "ix_itens_analise" in indexes
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
