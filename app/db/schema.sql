-- Schema do ReqScriba (SQLite). Idempotente: aplicado a cada inicialização.

CREATE TABLE IF NOT EXISTS projetos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    descricao TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
    nome_arquivo TEXT NOT NULL,
    caminho_arquivo TEXT NOT NULL,
    tipo_conteudo TEXT,
    tamanho_bytes INTEGER NOT NULL,
    duracao_segundos REAL,
    status TEXT NOT NULL,
    mensagem_erro TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    processado_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_audios_projeto ON audios(projeto_id);

CREATE TABLE IF NOT EXISTS segmentos_transcricao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_id INTEGER NOT NULL REFERENCES audios(id) ON DELETE CASCADE,
    posicao INTEGER NOT NULL,
    inicio_segundos REAL NOT NULL,
    fim_segundos REAL NOT NULL,
    texto TEXT NOT NULL,
    texto_original TEXT NOT NULL,
    editado INTEGER NOT NULL DEFAULT 0,
    atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_segmentos_audio ON segmentos_transcricao(audio_id, posicao);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
    nome_arquivo TEXT NOT NULL,
    caminho_arquivo TEXT NOT NULL,
    tipo_conteudo TEXT,
    tamanho_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    mensagem_erro TEXT,
    texto_extraido TEXT,
    total_paginas INTEGER,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    processado_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_documentos_projeto ON documentos(projeto_id);

CREATE TABLE IF NOT EXISTS trechos_fonte (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
    tipo_fonte TEXT NOT NULL CHECK (tipo_fonte IN ('audio', 'documento')),
    fonte_id INTEGER NOT NULL,
    nome_arquivo TEXT NOT NULL,
    posicao INTEGER NOT NULL,
    texto TEXT NOT NULL,
    localizacao TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trechos_fonte ON trechos_fonte(tipo_fonte, fonte_id, posicao);

CREATE TABLE IF NOT EXISTS historias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
    codigo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    perfil TEXT,
    funcionalidade TEXT,
    beneficio TEXT,
    historia TEXT NOT NULL,
    criterios_aceitacao TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    informacoes_ausentes TEXT NOT NULL DEFAULT '[]',
    afirmacoes_sem_evidencia TEXT NOT NULL DEFAULT '[]',
    sugestao_scriba TEXT,
    origem TEXT NOT NULL,
    versao_atual INTEGER NOT NULL DEFAULT 1,
    qualidade_porcentagem REAL,
    qualidade_status TEXT,
    qualidade_avaliacao_id INTEGER,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (projeto_id, codigo)
);
CREATE INDEX IF NOT EXISTS ix_historias_projeto ON historias(projeto_id, status);

CREATE TABLE IF NOT EXISTS versoes_historia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    historia_id INTEGER NOT NULL REFERENCES historias(id) ON DELETE CASCADE,
    numero INTEGER NOT NULL,
    tipo_alteracao TEXT NOT NULL CHECK (tipo_alteracao IN ('manual', 'automatica')),
    origem_alteracao TEXT NOT NULL,
    conteudo TEXT NOT NULL,
    evidencia_ids TEXT NOT NULL DEFAULT '[]',
    responsavel TEXT NOT NULL,
    sugestao_atualizacao_id INTEGER,
    observacao TEXT,
    criado_em TEXT NOT NULL,
    UNIQUE (historia_id, numero)
);

CREATE TABLE IF NOT EXISTS evidencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    historia_id INTEGER NOT NULL REFERENCES historias(id) ON DELETE CASCADE,
    tipo_fonte TEXT NOT NULL,
    fonte_id INTEGER NOT NULL,
    nome_arquivo TEXT NOT NULL,
    trecho_id INTEGER,
    trecho TEXT NOT NULL,
    localizacao TEXT NOT NULL,
    afirmacao TEXT,
    origem TEXT NOT NULL CHECK (origem IN ('extracao', 'atualizacao')),
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evidencias_historia ON evidencias(historia_id);

CREATE TABLE IF NOT EXISTS avaliacoes_qualidade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    historia_id INTEGER NOT NULL REFERENCES historias(id) ON DELETE CASCADE,
    versao_historia INTEGER NOT NULL,
    porcentagem REAL NOT NULL,
    status TEXT NOT NULL,
    criterios_atendidos INTEGER NOT NULL,
    avaliacao_criterios TEXT NOT NULL,
    analise_transversal_conjunto TEXT NOT NULL,
    lacunas TEXT NOT NULL DEFAULT '[]',
    reescrita_sugerida TEXT,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_avaliacoes_historia ON avaliacoes_qualidade(historia_id, id);

CREATE TABLE IF NOT EXISTS perguntas_followup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
    historia_id INTEGER NOT NULL REFERENCES historias(id) ON DELETE CASCADE,
    avaliacao_id INTEGER,
    defeito_chave TEXT NOT NULL,
    tipo_defeito TEXT NOT NULL,
    descricao_defeito TEXT NOT NULL,
    pergunta TEXT NOT NULL,
    justificativa TEXT NOT NULL,
    status TEXT NOT NULL,
    resposta TEXT,
    resposta_tipo_fonte TEXT,
    resposta_fonte_id INTEGER,
    editada INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    respondida_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_perguntas_projeto ON perguntas_followup(projeto_id, status);

CREATE TABLE IF NOT EXISTS sugestoes_atualizacao (
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
CREATE INDEX IF NOT EXISTS ix_sugestoes_projeto ON sugestoes_atualizacao(projeto_id, status);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    projeto_id INTEGER NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    status TEXT NOT NULL,
    parametros TEXT NOT NULL DEFAULT '{}',
    resultado TEXT,
    mensagem_erro TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    concluido_em TEXT
);
