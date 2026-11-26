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


# -- Critérios: do JSON da história para a tabela criterios_aceitacao --------
from app.db.database import SCHEMA_PATH  # noqa: E402

LEGACY_CRITERIA = (
    '[{"id": "CA01", "titulo": "PDF", "criterio": "Dado uma turma, quando emitir, então gera o PDF."},'
    ' {"id": "CA02", "titulo": "", "criterio": "  "},'
    ' {"id": "CA03", "criterio": "Dado x, quando y, então z.", "dado": "x", "quando": "y", "entao": "z", "origem": "ia"}]'
)


def legacy_database(path):
    """Banco no formato anterior: sem as colunas novas e com os critérios no JSON da história."""
    ddl = "\n".join(line for line in SCHEMA_PATH.read_text(encoding="utf-8").splitlines()
                    if "criterio_ids" not in line and "afirmacoes_sustentadas" not in line)
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    conn.executescript(f"""
        INSERT INTO projetos (titulo, criado_em, atualizado_em) VALUES ('Escola', 'x', 'x');
        INSERT INTO historias (projeto_id, codigo, titulo, historia, criterios_aceitacao, status, origem, versao_atual,
                               criado_em, atualizado_em)
        VALUES (1, 'US-001', 'Boletim', 'Como a, quero b, para c.', '{LEGACY_CRITERIA}', 'em_revisao', 'ia', 2, 'x', 't2');
        INSERT INTO versoes_historia (historia_id, numero, tipo_alteracao, origem_alteracao, conteudo, responsavel, criado_em)
        VALUES (1, 1, 'automatica', 'geracao_ia', '{{}}', 'sistema', 'x'), (1, 2, 'manual', 'edicao_manual', '{{}}', 'ana', 'x');
    """)
    conn.close()


def test_legacy_criteria_move_to_their_own_table(tmp_path):
    path = tmp_path / "legado.db"
    legacy_database(path)
    database = Database(path)
    database.initialize()
    database.initialize()  # idempotente: não duplica os critérios

    conn = database.connect()
    try:
        rows = conn.execute("SELECT codigo, posicao, titulo, criterio, dado, origem, versao_historia, status "
                            "FROM criterios_aceitacao ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [
            ("CA01", 1, "PDF", "Dado uma turma, quando emitir, então gera o PDF.", None, "legado", 2, "vigente"),
            ("CA02", 2, "Critério 2", "Dado x, quando y, então z.", "x", "ia", 2, "vigente"),  # vazio descartado
        ]
        versions = dict(conn.execute("SELECT numero, criterio_ids FROM versoes_historia").fetchall())
        assert versions == {1: "[]", 2: "[1, 2]"}  # só a versão atual tinha esses critérios com certeza
        legacy = conn.execute("SELECT criterios_aceitacao FROM historias").fetchone()[0]
        assert legacy == LEGACY_CRITERIA  # coluna antiga preservada (rollback)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(evidencias)")}
        assert "afirmacoes_sustentadas" in columns
        assert [r[0] for r in conn.execute("SELECT nome FROM migracoes")] == ["criterios_tabela_v1"]
    finally:
        conn.close()



OLD_SUGGESTIONS_DDL = """
DROP TABLE sugestoes_atualizacao;
CREATE TABLE sugestoes_atualizacao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
    historia_id INTEGER REFERENCES historias(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL CHECK (tipo IN ('complementa', 'contradiz', 'novo_requisito')),
    justificativa TEXT NOT NULL,
    conteudo_atual TEXT,
    conteudo_proposto TEXT NOT NULL,
    evidencias TEXT NOT NULL DEFAULT '[]',
    fontes TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    editada INTEGER NOT NULL DEFAULT 0,
    decidido_por TEXT,
    decidido_em TEXT,
    versao_aplicada INTEGER,
    historia_criada_id INTEGER,
    job_id TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
INSERT INTO sugestoes_atualizacao (projeto_id, historia_id, tipo, justificativa, conteudo_proposto, status,
                                   criado_em, atualizado_em)
VALUES (1, 1, 'contradiz', 'antiga', '{}', 'pendente', 'x', 'x');
"""


def test_old_suggestion_check_accepts_new_impact_categories(tmp_path):
    path = tmp_path / "sugestoes.db"
    legacy_database(path)
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SUGGESTIONS_DDL)
    conn.close()
    database = Database(path)
    database.initialize()
    database.initialize()
    conn = database.connect()
    try:
        conn.execute("INSERT INTO sugestoes_atualizacao (projeto_id, tipo, justificativa, conteudo_proposto, status, "
                     "criado_em, atualizado_em) VALUES (1, 'nova_necessidade', 'nova', '{}', 'pendente', 'x', 'x')")
        rows = conn.execute("SELECT tipo, detalhes, versao_base FROM sugestoes_atualizacao ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [("contradiz", "{}", None), ("nova_necessidade", "{}", None)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()

