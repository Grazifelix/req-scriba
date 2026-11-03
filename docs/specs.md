# Especificações Técnicas — ReqScriba Backend v1

## 1. Arquitetura do backend (resumo)

API REST em FastAPI organizada em camadas (detalhes em `architecture.md`):

```
HTTP ─▶ api/routers ─▶ services ─▶ repositories ─▶ SQLite
                         │  └────▶ integrations ─▶ Ollama (LLM, embeddings) · ChromaDB · Whisper · disco
                         └──────▶ domain (regras puras: QUS, formato de HU, similaridade, chunking)
workers/tasks.py  ◀─ BackgroundTasks (transcrição, extração, geração, análise de atualização)
```

## 2. Estrutura de pastas

```
reqscriba/
├── CLAUDE.md · README.md · Makefile · requirements*.txt · .env.example
├── docs/                         # PRD, specs, regras, arquitetura, tarefas, testes, UX, segurança
├── app/
│   ├── main.py                   # create_app(): FastAPI, CORS, handlers de erro, routers
│   ├── core/
│   │   ├── config.py             # Settings (variáveis REQSCRIBA_*)
│   │   ├── errors.py             # hierarquia AppError → JSON
│   │   ├── container.py          # Container de dependências (DB, LLM, embeddings, vetores, ASR, storage)
│   │   └── time.py               # utc_now_iso()
│   ├── db/
│   │   ├── database.py           # conexão sqlite3, PRAGMAs, inicialização do schema
│   │   ├── schema.sql            # DDL completo
│   │   └── unit_of_work.py       # UnitOfWork: transação + repositórios
│   ├── repositories/             # SQL puro por agregado
│   │   ├── base.py · projects.py · sources.py (áudios, segmentos, documentos, trechos)
│   │   ├── stories.py (histórias, versões, evidências) · workflow.py (avaliações, perguntas, sugestões, jobs)
│   ├── domain/                   # regras puras, sem I/O
│   │   ├── enums.py · qus.py · story_format.py · text.py · chunking.py
│   ├── integrations/
│   │   ├── ollama.py             # OllamaClient (requests): chat JSON + embeddings + health
│   │   ├── embeddings.py         # OllamaEmbedder, HashEmbedder (testes/dev offline)
│   │   ├── vector_store.py       # ChromaVectorStore, InMemoryVectorStore
│   │   ├── transcriber.py        # FasterWhisperTranscriber
│   │   ├── extractors.py         # PDF/DOCX/TXT/MD → páginas de texto
│   │   └── storage.py            # FileStorage (uploads em disco, nomes saneados)
│   ├── llm/
│   │   ├── prompts.py            # prompts versionados (extração, QUS, conjunto, perguntas, impacto)
│   │   └── parsing.py            # normalização/validação das respostas JSON do LLM
│   ├── services/
│   │   ├── common.py · projects.py · audios.py · documents.py · indexing.py · retrieval.py
│   │   ├── stories.py · versioning.py · evidence.py · generation.py
│   │   ├── quality.py · questions.py · updates.py · jobs.py · presenters.py
│   ├── workers/tasks.py          # tarefas em background (abrem a própria UnitOfWork)
│   ├── schemas/                  # Pydantic: common, projects, sources, stories, workflow
│   └── api/
│       ├── deps.py               # injeção: container, uow, responsável
│       └── routers/              # health, projects (+busca), sources (áudios, transcrição, documentos),
│                                 # stories (+evidências, versões), workflow (qualidade, perguntas, atualizações, jobs)
├── tests/                        # pytest (fakes de LLM/ASR/embeddings)
└── data/                         # criado em runtime: reqscriba.db, chroma/, uploads/
```

## 3. Modelos de dados (SQLite)

Convenção: colunas em português; JSON armazenado como TEXT; datas ISO-8601 UTC; FKs com
`ON DELETE CASCADE`. DDL completo em `app/db/schema.sql`.

### 3.1 `projetos`
| coluna | tipo | obs |
|---|---|---|
| id | INTEGER PK | |
| titulo | TEXT NOT NULL | 1–200 caracteres |
| descricao | TEXT | até 5000 |
| criado_em / atualizado_em | TEXT | |

Campos **derivados** na resposta: `total_audios`, `total_documentos`, `total_historias_prontas`
(aprovadas), `total_historias_pendentes` (em_revisao + falta_informacao), `total_historias_desaprovadas`,
`qualidade_geral` (média RN06), `backlog` (ids das HU não desaprovadas, ordenadas por código),
`backlog_aprovado` (ids das aprovadas).

### 3.2 `audios`
id, projeto_id, nome_arquivo, caminho_arquivo, tipo_conteudo, tamanho_bytes, duracao_segundos,
status (`pendente|processando|concluido|erro`), mensagem_erro, criado_em, atualizado_em, processado_em.
Resposta inclui `status_rotulo` ("Transcrito", "Transcrição em andamento", …), `total_segmentos`
e `url_arquivo`.

### 3.3 `segmentos_transcricao`
id, audio_id, posicao, inicio_segundos, fim_segundos, texto, texto_original, editado (0/1), atualizado_em.
`texto` é a versão revisada (usada nas extrações); `texto_original` preserva a saída do ASR.

### 3.4 `documentos`
id, projeto_id, nome_arquivo, caminho_arquivo, tipo_conteudo, tamanho_bytes, status, mensagem_erro,
texto_extraido, total_paginas, criado_em, atualizado_em, processado_em.

### 3.5 `trechos_fonte` (unidades de RAG e de evidência)
id, projeto_id, tipo_fonte (`audio|documento`), fonte_id, nome_arquivo (desnormalizado para
evidências e prompts), posicao, texto, localizacao (JSON), criado_em.
- Áudio: `{"inicio_segundos", "fim_segundos", "segmento_ids": [...]}`
- Documento: `{"pagina", "inicio_caractere", "fim_caractere", "indice_trecho"}`
O mesmo `id` é usado no ChromaDB (`"trecho-<id>"`) com metadados `projeto_id`, `tipo_fonte`,
`fonte_id`, `fonte_chave` (`"documento:3"`), `nome_arquivo`.

### 3.6 `historias`
id, projeto_id, codigo (`US-001` sequencial por projeto), titulo, perfil, funcionalidade, beneficio,
historia (texto derivado do formato padrão), criterios_aceitacao (JSON `[{"id":"CA01","titulo","criterio"}]`),
status (`em_revisao|falta_informacao|aprovada|desaprovada`), informacoes_ausentes (JSON
`[{"elemento","descricao"}]`), afirmacoes_sem_evidencia (JSON `[str]`), sugestao_scriba,
origem (`ia|manual`), versao_atual, qualidade_porcentagem, qualidade_status, qualidade_avaliacao_id,
criado_em, atualizado_em.

### 3.7 `versoes_historia`
id, historia_id, numero, tipo_alteracao (`manual|automatica`), origem_alteracao
(`criacao_manual|geracao_ia|edicao_manual|sugestao_ia|sugestao_ia_editada`), conteudo (JSON snapshot:
titulo, perfil, funcionalidade, beneficio, historia, criterios_aceitacao), evidencia_ids (JSON),
responsavel, sugestao_atualizacao_id, observacao, criado_em. `UNIQUE(historia_id, numero)`.

### 3.8 `evidencias` (imutáveis)
id, historia_id, tipo_fonte, fonte_id, nome_arquivo, trecho_id, trecho (cópia do texto), localizacao
(cópia), afirmacao (o que a evidência sustenta, opcional), origem (`extracao|atualizacao`), criado_em.

### 3.9 `avaliacoes_qualidade`
id, historia_id, versao_historia, porcentagem, status, criterios_atendidos, avaliacao_criterios (JSON),
analise_transversal_conjunto (JSON), lacunas (JSON), reescrita_sugerida, criado_em.

### 3.10 `perguntas_followup`
id, projeto_id, historia_id, avaliacao_id, defeito_chave, tipo_defeito, descricao_defeito, pergunta,
justificativa, status (`pendente|aprovada|rejeitada|respondida`), resposta, resposta_tipo_fonte,
resposta_fonte_id, editada, criado_em, atualizado_em, respondida_em.

### 3.11 `sugestoes_atualizacao`
id, projeto_id, historia_id (NULL = novo requisito), tipo (`complementa|contradiz|novo_requisito`),
justificativa, conteudo_atual (JSON), conteudo_proposto (JSON), evidencias (JSON com trechos citados),
fontes (JSON `["audio:4"]`), status (`pendente|aprovada|rejeitada`), editada (0/1), decidido_por, decidido_em,
versao_aplicada, historia_criada_id, job_id, criado_em, atualizado_em.

### 3.12 `jobs`
id (uuid hex), projeto_id, tipo (`geracao_historias|analise_atualizacao`), status
(`pendente|processando|concluido|erro`), parametros (JSON), resultado (JSON), mensagem_erro,
criado_em, atualizado_em, concluido_em.

## 4. Contratos JSON principais

### 4.1 História de Usuário (`GET /historias/{id}`)
```json
{
  "id": 7, "projeto_id": 1, "codigo": "US-007",
  "titulo": "Exportar relatório mensal",
  "historia": "Como gestor financeiro, quero exportar o relatório mensal em PDF, para [A DEFINIR: beneficio].",
  "perfil": "gestor financeiro", "funcionalidade": "exportar o relatório mensal em PDF", "beneficio": null,
  "status": "falta_informacao", "status_rotulo": "Falta informação", "validada": false,
  "qualidade_geral": {"porcentagem": 61.54, "status": "Regular", "criterios_atendidos": 8,
                      "total_criterios": 13, "avaliacao_id": 3, "versao_historia": 2, "desatualizada": false},
  "criterios_aceitacao": [{"id": "CA01", "titulo": "Exportação", "criterio": "Dado ..., quando ..., então ..."}],
  "fontes_audio": [{"evidencia_id": 11, "audio_id": 2, "nome_arquivo": "reuniao1.mp3",
                    "trecho": "...", "inicio_segundos": 61.2, "fim_segundos": 79.8,
                    "url_momento": "/api/v1/audios/2/arquivo#t=61.2"}],
  "fontes_documentos": [{"evidencia_id": 12, "documento_id": 3, "nome_arquivo": "escopo.pdf",
                         "trecho": "...", "pagina": 4, "inicio_caractere": 120, "fim_caractere": 980}],
  "possui_suporte_documental": true,
  "afirmacoes_sem_evidencia": [],
  "informacoes_ausentes": [{"elemento": "beneficio", "descricao": "O benefício não foi mencionado."}],
  "criterios_qus": { "avaliacao_criterios": {"well_formed": {"resultado": "Atende", "evidencia": "...", "acao": "..."}},
                     "analise_transversal_conjunto": {"unique": {"resultado": "Atende", "observacao": "..."}},
                     "reescrita_sugerida": "..." },
  "sugestao_scriba": "Como gestor financeiro, quero ..., para ...",
  "atualizacoes_pendentes": 1,
  "versao_atual": 2,
  "versoes": [{"numero": 1, "tipo_alteracao": "automatica", "origem_alteracao": "geracao_ia",
               "historia": "...", "data_hora": "2026-10-31T12:00:00+00:00", "responsavel": "sistema"}],
  "origem": "ia", "criado_em": "...", "atualizado_em": "..."
}
```

### 4.2 Qualidade (QUS)
13 critérios (Lucassen et al., 2016):
- **Individuais (8, avaliados pelo LLM com regras determinísticas de reforço):** `well_formed`,
  `atomic`, `minimal`, `conceptually_sound`, `problem_oriented`, `unambiguous`, `full_sentence`, `estimatable`.
- **De conjunto (5):** `conflict_free`, `unique`, `uniform`, `independent`, `complete`.

Resultados possíveis: `Atende`, `Atenção`, `Não atende` (variações do LLM são normalizadas;
valor desconhecido → `Atenção`). **Somente `Atende` pontua.**

Regras determinísticas (sobrepõem o LLM por serem verificáveis):
- `well_formed`: `Não atende` se faltar perfil ou funcionalidade.
- `unique`: `Não atende` se similaridade textual ≥ 0,90 com outra HU não desaprovada do projeto.
- `uniform`: `Atende` se a HU segue o template padrão completo; caso contrário `Atenção`.
- `conflict_free` e `independent`: `Atende` automaticamente quando a HU é a única do backlog.

Porcentagem = `round(atendidos / 13 × 100, 2)` → 1: 7,69 · 2: 15,38 · … · 12: 92,31 · 13: 100,00.
Faixa: `<25 Péssima`, `<50 Baixa`, `<75 Regular`, `<100 Alta`, `100 Excelente`.

### 4.3 Defeitos (`GET /historias/{id}/defeitos`)
União de: critérios QUS ≠ Atende da última avaliação (`qus:<criterio>`), lacunas da avaliação e
`informacoes_ausentes` (`lacuna:<n>`), afirmações sem evidência (`sem_evidencia:<n>`). Cada defeito:
`{"chave", "tipo", "criterio", "descricao", "acao_sugerida"}`.

### 4.4 Erro
```json
{"erro": {"codigo": "validacao", "mensagem": "Formato de arquivo não suportado: .exe", "detalhes": {"extensoes_aceitas": [".mp3"]}}}
```

## 5. Estratégia de persistência

- **SQLite** (`data/reqscriba.db`) com `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`.
  Uma conexão por requisição/tarefa via `UnitOfWork` (commit no sucesso, rollback em exceção).
  Schema idempotente (`CREATE TABLE IF NOT EXISTS`) aplicado na inicialização.
- **ChromaDB** (`data/chroma/`), coleção `reqscriba_trechos`, espaço `cosine`. Os embeddings são
  calculados pelo backend (Ollama `bge-m3`) e enviados explicitamente — a coleção não depende de
  função de embedding interna. SQLite é a fonte de verdade; Chroma é um índice reconstruível.
- **Arquivos** em `data/uploads/projeto_<id>/<uuid>_<nome_saneado>`.
- Exclusão de projeto/fonte remove linhas (cascade), vetores (`where`) e arquivos.

## 6. Chunking e embeddings

- **Documentos:** divisão recursiva por fronteiras estruturais, página a página (a localização da
  página é sempre exata). Alvo de 1000 caracteres, sobreposição de 150. Corte preferencial em
  `\n\n` → `\n` → `. ` → espaço, buscando a fronteira na segunda metade da janela.
- **Transcrições:** janelas de segmentos consecutivos até 800 caracteres, sem cortar segmentos,
  preservando `inicio_segundos`/`fim_segundos`. Reconstruídas quando um segmento é corrigido (US-03 CA03).
- **Embeddings:** `bge-m3` via `POST /api/embed` (lotes de 32). Em testes, `HashEmbedder` determinístico.

## 7. Estratégia de LLM

- `POST {OLLAMA_URL}/api/chat` com `format: "json"`, `stream: false`, `temperature 0.1`, `num_ctx 8192`.
- Resposta inválida → uma nova tentativa pedindo JSON estrito → `ExternalServiceError` (HTTP 502/erro no job).
- Fontes enviadas como blocos rotulados `[F1] (audio reuniao.mp3 00:01:02–00:01:20) texto…`. O LLM cita
  rótulos; o backend resolve rótulos → `trechos_fonte` e copia o texto para `evidencias`. Rótulos
  inexistentes são descartados. HU sem rótulo válido recebe afirmação "sem trecho de origem".
- **Geração:** lotes de até 6000 caracteres. Com áudios: cada lote de janelas de transcrição é
  enriquecido com até 4 trechos de documentos recuperados por RAG (filtro pelas fontes selecionadas);
  trechos de documentos não usados são processados em lotes próprios. Deduplicação final por
  similaridade (≥ 0,85) mescla evidências e critérios.
- **Análise de atualização:** para cada HU não desaprovada, busca vetorial restrita às novas fontes
  (top-4) → LLM classifica `sem_relacao|complementa|contradiz` e propõe conteúdo. Trechos novos não
  citados por nenhuma HU passam pela extração → sugestões `novo_requisito` (descartando duplicatas ≥ 0,85).

## 8. Estratégia de validação

- Entrada HTTP: Pydantic (`extra="forbid"`, limites de tamanho, enums). Strings são aparadas.
- Uploads: extensão na lista permitida, assinatura binária (magic bytes) coerente, tamanho máximo,
  arquivo não vazio. Texto: UTF-8 (fallback latin-1) sem bytes nulos.
- Regras de negócio no serviço: projeto existe, fontes pertencem ao projeto e estão `concluido`,
  transições de status válidas (ex.: só sugestão `pendente` pode ser decidida).
- Saída do LLM: normalização de chaves/valores, descarte de itens inválidos, limites de tamanho.

## 9. Tratamento de erros

| Exceção | HTTP | codigo |
|---|---|---|
| `NotFoundError` | 404 | `nao_encontrado` |
| `BusinessValidationError` | 422 | `validacao` |
| `ConflictError` | 409 | `conflito` |
| `ExternalServiceError` (Ollama/Chroma/ASR) | 502 | `servico_externo` |
| `RequestValidationError` (Pydantic) | 422 | `entrada_invalida` (detalhes por campo) |
| `HTTPException` (404 de rota etc.) | status original | `http_<status>` |
| Exceção não tratada | 500 | `erro_interno` (mensagem genérica; detalhe só no log) |

Falhas em tarefas de background nunca derrubam a API: a fonte/job vai para `erro` com
`mensagem_erro` legível e pode ser reprocessada (`POST .../reprocessar`).

## 10. Estados vazios

Listas vazias retornam `200` com `{"itens": [], "total": 0}`. Casos específicos:
- Transcrição ainda não concluída → `200` com `segmentos: []` e o `status` atual do áudio.
- HU sem avaliação → `qualidade_geral: null`, `criterios_qus: null`.
- Projeto sem HU avaliadas → `qualidade_geral: {"porcentagem": null, "status": "Sem avaliação"}`.
- Geração sem necessidades identificadas → job `concluido` com `historias_criadas: []` e `mensagem`.
- Perguntas sem defeitos → `perguntas: []` + `limitacoes: ["Nenhum defeito identificado..."]`.
- Comparação com só uma avaliação → `409 conflito` explicando que são necessárias duas.

## 11. Estados de carregamento (operações assíncronas)

| Operação | Resposta imediata | Como acompanhar |
|---|---|---|
| Upload de áudio | `202` + áudio `pendente` | `GET /audios/{id}` (`pendente → processando → concluido/erro`) |
| Upload de documento | `202` + documento `pendente` | `GET /documentos/{id}` |
| Gerar HU | `202` + job | `GET /jobs/{id}` (`resultado.historias_criadas`) |
| Analisar novas fontes | `202` + job | `GET /jobs/{id}` (`resultado.sugestoes_criadas`) |
| Avaliar HU / gerar perguntas | síncrono (uma HU, segundos) | resposta direta |

## 12. Estratégia de testes

Pirâmide: domínio (unitário) → serviços com SQLite temporário + fakes → API com `TestClient`.
Fakes: `FakeLLM` (roteia pelo marcador `### TAREFA:` do prompt), `FakeTranscriber`, `HashEmbedder`,
`InMemoryVectorStore`. Detalhes em `test-plan.md`.

## 13. Configuração (variáveis de ambiente)

| Variável | Padrão |
|---|---|
| `REQSCRIBA_DATA_DIR` | `./data` |
| `REQSCRIBA_OLLAMA_URL` | `http://localhost:11434` |
| `REQSCRIBA_LLM_MODEL` | `llama3.2:3b` |
| `REQSCRIBA_EMBEDDING_MODEL` | `bge-m3` |
| `REQSCRIBA_OLLAMA_TIMEOUT` | `300` |
| `REQSCRIBA_LLM_TEMPERATURE` / `REQSCRIBA_LLM_NUM_CTX` | `0.1` / `8192` |
| `REQSCRIBA_EMBEDDINGS_PROVIDER` | `ollama` (`hash` para modo offline) |
| `REQSCRIBA_VECTOR_STORE` | `chroma` (`memory` para testes) |
| `REQSCRIBA_WHISPER_MODEL` / `_DEVICE` / `_COMPUTE_TYPE` | `small` / `cpu` / `int8` |
| `REQSCRIBA_TRANSCRIPTION_LANGUAGE` | `pt` |
| `REQSCRIBA_MAX_AUDIO_MB` / `REQSCRIBA_MAX_DOCUMENT_MB` | `200` / `20` |
| `REQSCRIBA_CHUNK_SIZE` / `REQSCRIBA_CHUNK_OVERLAP` | `1000` / `150` |
| `REQSCRIBA_TRANSCRIPT_WINDOW_CHARS` | `800` |
| `REQSCRIBA_GENERATION_BATCH_CHARS` | `6000` |
| `REQSCRIBA_RAG_TOP_K` | `4` |
| `REQSCRIBA_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` |
| `REQSCRIBA_LOG_LEVEL` | `INFO` |

## 14. Observabilidade

Logging estruturado por módulo (`logging`), incluindo duração das chamadas ao LLM e ao ASR, ids de
job/fonte. Conteúdo de transcrições e documentos **não** é logado (apenas tamanhos e ids).
`GET /api/v1/health` verifica SQLite, ChromaDB, Ollama (modelos disponíveis) e disponibilidade do ASR.
