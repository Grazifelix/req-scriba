# Especificações Técnicas — ReqScriba Backend v1

## 1. Arquitetura do backend (resumo)

API REST em FastAPI organizada em camadas (detalhes em `architecture.md`):

```
HTTP ─▶ api/routers ─▶ services ─▶ repositories ─▶ SQLite
                         │  └────▶ integrations ─▶ Provedor de LLM/embeddings · ChromaDB · Whisper · disco
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
│   │   ├── analyses.py (análises e itens de análise)
│   ├── domain/                   # regras puras, sem I/O
│   │   ├── enums.py · qus.py · story_format.py · text.py · chunking.py
│   ├── integrations/
│   │   ├── llm.py                # Protocolo LLMClient, retentativa de JSON, POST e saúde comuns
│   │   ├── ollama.py             # OllamaClient: API nativa do Ollama
│   │   ├── openai_compatible.py  # OpenAICompatibleClient: servidores compatíveis com a API da OpenAI
│   │   ├── embeddings.py         # RemoteEmbedder (em lotes), HashEmbedder (testes/dev offline)
│   │   ├── vector_store.py       # ChromaVectorStore, InMemoryVectorStore
│   │   ├── transcriber.py        # FasterWhisperTranscriber (local) e RemoteTranscriber (HTTP)
│   │   ├── extractors.py         # PDF/DOCX/TXT/MD → páginas de texto
│   │   └── storage.py            # FileStorage (uploads em disco, nomes saneados)
│   ├── llm/
│   │   ├── prompts.py            # prompts versionados (extração, QUS, conjunto, perguntas, impacto)
│   │   └── parsing.py            # normalização/validação das respostas JSON do LLM
│   ├── services/
│   │   ├── common.py · projects.py · audios.py · documents.py · indexing.py · retrieval.py
│   │   ├── stories.py · versioning.py · evidence.py · generation.py · candidates.py
│   │   ├── context.py · consolidation.py · analysis.py (análise das fontes + CRUD)
│   │   ├── quality.py · questions.py · updates.py · jobs.py · presenters.py
│   ├── workers/
│   │   ├── tasks.py              # tarefas em background (abrem a própria UnitOfWork)
│   │   └── reindex.py            # `make reindex`: reconstrói o índice vetorial
│   ├── schemas/                  # Pydantic: common, projects, sources, analyses, stories, workflow
│   └── api/
│       ├── deps.py               # injeção: container, uow, responsável
│       └── routers/              # health, projects (+busca), sources (áudios, transcrição, documentos),
│                                 # analyses (análises e itens), stories (+evidências, versões),
│                                 # workflow (qualidade, perguntas, atualizações, jobs)
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
`backlog_aprovado` (ids das aprovadas), `descricao_sistema` (campo `sistema` da análise das fontes mais
recente; `null` sem análise). A interface exibe `descricao_sistema` como descrição do projeto; `descricao`
continua no contrato, mas não é mais editada pela interface.

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
id (uuid hex), projeto_id, tipo (`geracao_historias|analise_fontes|analise_atualizacao|avaliacao_qualidade`), status
(`pendente|processando|concluido|erro`), parametros (JSON), resultado (JSON), mensagem_erro,
criado_em, atualizado_em, concluido_em.

### 3.13 `analises`
id, projeto_id, job_id (job `analise_fontes` que a produziu; NULL se manual), origem (`ia|manual`), sistema,
fontes (JSON `[{"tipo_fonte","fonte_id","nome_arquivo"}]`, acumuladas desde a primeira análise), revisada (0/1),
revisada_por, revisada_em, criado_em, atualizado_em. Uma por execução do job de análise; `revisada = 1` significa
aprovada pelo analista, condição para gerar HU.

### 3.14 `itens_analise`
id, analise_id, tipo (`participante|usuario|requisito_negocio|funcionalidade|termo|fora_de_escopo`), codigo
(`RN1..`/`FN1..`; NULL nos demais tipos), texto, complemento (papel na reunião · descrição do usuário ·
usuário da funcionalidade · significado do termo, NULL se as fontes não o definem · motivo de estar fora de
escopo), referencias (JSON, só funcionalidades: códigos RN),
evidencias (JSON, cópias de trechos como em `sugestoes_atualizacao`), historia_ids (JSON, HU geradas a partir
da funcionalidade), origem (`ia|manual`), editado (0/1), posicao, criado_em, atualizado_em.

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

### 4.4 Análise das fontes (`GET /analises/{id}`)
```json
{
  "id": 3, "projeto_id": 1, "job_id": "a1b2…", "origem": "ia", "sistema": "Pedidos por tablet na mesa",
  "fontes": [{"tipo_fonte": "audio", "fonte_id": 4, "nome_arquivo": "kickoff.mp3"}],
  "revisada": false, "revisada_por": null, "revisada_em": null,
  "totais": {"participante": 2, "usuario": 1, "requisito_negocio": 1, "funcionalidade": 1, "termo": 1, "fora_de_escopo": 1},
  "itens": [
    {"id": 10, "tipo": "funcionalidade", "tipo_rotulo": "Funcionalidade", "codigo": "FN1",
     "texto": "fazer pedidos pelo tablet da mesa", "complemento": "cliente", "referencias": ["RN1"],
     "evidencias": [{"trecho_id": 52, "tipo_fonte": "audio", "fonte_id": 4, "nome_arquivo": "kickoff.mp3",
                     "texto": "...", "localizacao": {"inicio_segundos": 42.0, "fim_segundos": 90.1},
                     "url_momento": "/api/v1/audios/4/arquivo#t=42.0"}],
     "possui_evidencia": true, "historia_ids": [31], "origem": "ia", "editado": false}
  ],
  "criado_em": "...", "atualizado_em": "..."
}
```
O resultado do job `analise_fontes` traz `analise_id` (NULL se o modelo não devolveu análise),
`analise_anterior_id`, `modo` (`chamada_unica|lotes`), `lotes`, `lotes_com_erro` e `contexto`. O resultado do
job de geração traz `modo` e `analise_id` (a análise aprovada usada, também gravada em `parametros`).

### 4.5 Sugestão de atualização (`GET /sugestoes-atualizacao/{id}`)
Além de `evidencias` (cópias cruas dos trechos), a resposta traz `fontes_audio` e `fontes_documentos`,
no mesmo formato das fontes de uma HU (áudio com `inicio_formatado` e `url_momento`; documento com
`pagina`), mas sem `evidencia_id`, `origem` e `criado_em`, que só existem depois da aprovação. O
frontend usa esses campos para mostrar em quais fontes a IA se baseou.

### 4.6 Erro
```json
{"erro": {"codigo": "validacao", "mensagem": "Formato de arquivo não suportado: .exe", "detalhes": {"extensoes_aceitas": [".mp3"]}}}
```

## 5. Estratégia de persistência

- **SQLite** (`data/reqscriba.db`) com `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`.
  Uma conexão por requisição/tarefa via `UnitOfWork` (commit no sucesso, rollback em exceção).
  Schema idempotente (`CREATE TABLE IF NOT EXISTS`) aplicado na inicialização.
- **ChromaDB** (`data/chroma/`), uma coleção por modelo de embeddings (`reqscriba_trechos__<hash do modelo>`),
  espaço `cosine`. Os embeddings são calculados pelo provedor configurado e enviados explicitamente — a
  coleção não depende de função de embedding interna. SQLite é a fonte de verdade; Chroma é um índice
  reconstruível com `make reindex` (API parada), necessário ao trocar de modelo. Enquanto houver trechos
  sem vetor, `/health` retorna `banco_vetorial.reindexacao_pendente: true`.
- **Arquivos** em `data/uploads/projeto_<id>/<uuid>_<nome_saneado>`.
- Exclusão de projeto/fonte remove linhas (cascade), vetores (`where`) e arquivos.

## 6. Chunking e embeddings

- **Documentos:** divisão recursiva por fronteiras estruturais, página a página (a localização da
  página é sempre exata). Alvo de 1000 caracteres, sobreposição de 150. Corte preferencial em
  `\n\n` → `\n` → `. ` → espaço, buscando a fronteira na segunda metade da janela.
- **Transcrições:** janelas de segmentos consecutivos até 800 caracteres, sem cortar segmentos,
  preservando `inicio_segundos`/`fim_segundos`. Reconstruídas quando um segmento é corrigido (US-03 CA03).
- **Embeddings:** `bge-m3` por padrão, via `POST /api/embed` no Ollama ou `POST {base}/embeddings` em
  servidor compatível com OpenAI (lotes de 32). Em testes, `HashEmbedder` determinístico.

## 7. Estratégia de LLM

- Os serviços dependem apenas do protocolo `LLMClient`. O adaptador é escolhido por `REQSCRIBA_LLM_PROVIDER`:
  - `ollama` (padrão): `POST {URL}/api/chat` com `format: "json"`, `think: false`, `stream: false`, `temperature 0.1`, `num_ctx 8192`.
  - `openai_compatible`: `POST {base}/chat/completions` com `response_format: {"type": "json_object"}`,
    `temperature 0.1` e chave Bearer opcional.
- Resposta inválida → uma nova tentativa pedindo JSON estrito → `ExternalServiceError` (HTTP 502/erro no job).
- Fontes enviadas como blocos rotulados `[F1] (audio reuniao.mp3 00:01:02–00:01:20) texto…`. O LLM cita
  rótulos; o backend resolve rótulos → `trechos_fonte` e copia o texto para `evidencias`. Rótulos
  inexistentes são descartados, assim como códigos que não são fontes (`FN1`, `RN2`, `H1`). HU sem rótulo
  válido recebe afirmação "sem trecho de origem".
- **Prompt de análise das fontes (versão 3.0, `### TAREFA: ANALISE_FONTES`):** fontes (entre `<fontes>`) →
  `CONTEXTO GERAL` (modo lotes) → `ANÁLISE ATUAL DO PROJETO` (a análise mais recente, quando existe) →
  instruções ("ETAPA 1: Entender as fontes") e formato. O JSON pede só `analise` (sistema, participantes,
  usuários, requisitos de negócio `RN#`, funcionalidades `FN#`, fora de escopo). Com análise atual, o modelo
  devolve a análise completa e atualizada, mantendo os códigos.
- **Prompts em arquivo:** os prompts de geração ficam em `app/llm/templates/*.md` (cabeçalho com `versao`,
  `---`, corpo com placeholders `$nome`) e o contrato da resposta em `app/llm/schemas/*.schema.json`. Os
  blocos QUS (`_qus_individual.md`, `_qus_conjunto.md`) são compartilhados pela geração e pelo impacto. O
  resultado de cada job registra `versoes_prompt`. Com `REQSCRIBA_LLM_STRUCTURED_OUTPUT=true`, o schema vai
  ao provedor (Ollama `format`; OpenAI `response_format.json_schema`); sem isso, só se pede JSON. Em ambos os
  casos a resposta é normalizada pelo código.
- **Prompt de histórias (`### TAREFA: GERACAO_US_QUS`, template `geracao_us_qus.md`):** fontes → `CONTEXTO GERAL`
  → `ANALISE DO PROJETO` (a análise aprovada) → instruções e formato. **Não gera critérios de aceitação**: se o
  modelo devolver algum, ele é descartado e contado em `validacao.historias.criterios_descartados`. O JSON pede
  `historias` (com `id` `H#`, `funcionalidade_ref`, e `fontes` como lista de
  `{trecho_id, afirmacoes_sustentadas, citacao}`), `lacunas_conjunto` (`fluxo_incompleto`, `duplicidade`,
  `granularidade`), `conflitos_identificados` e `cobertura_funcional`. Na leitura: cada citação é conferida
  contra o texto do PRÓPRIO trecho citado (se não bater → `afirmacoes_sem_evidencia` "Citação não encontrada
  no trecho F#"); rótulo inexistente → "Referência a trecho inexistente"; o que cada trecho sustenta vai para
  `evidencias.afirmacao` ("Sustenta: perfil, funcionalidade"); lacunas e conflitos de conjunto →
  `informacoes_ausentes` das HU citadas; `funcionalidade_ref` e cobertura de FN inexistente são ignorados.
  Respostas no formato antigo (`fontes` como lista de rótulos e `citacao` única) continuam aceitas.
- **Prompt de critérios (`### TAREFA: GERACAO_CRITERIOS_ACEITACAO`, template `geracao_criterios_aceitacao.md`):**
  recebe várias HU por chamada (até `REQSCRIBA_GENERATION_BATCH_CHARS`), cada uma com os rótulos das suas
  evidências, mais até `REQSCRIBA_CRITERIA_RELATED_CHUNKS` trechos relacionados (RAG, marcados
  "relacionado") e a análise aprovada (contexto, não evidência). Devolve, por HU, critérios com `dado`,
  `quando`, `entao` e `fontes` (`trecho_id`, `afirmacoes_sustentadas`, `citacao`), e `lacunas_criterios`. O
  código monta `criterio` ("Dado …, quando …, então …."), remove duplicados (similaridade ≥ 0,85), limita a 8,
  troca rótulos por ids de trecho, sinaliza citação divergente, rótulo inexistente e critério sem fonte
  ("Critério sem trecho das fontes vinculado."). Trecho relacionado citado vira evidência da HU (afirmação
  "Sustenta critério de aceitação"). Sem base: zero critérios e lacuna `criterios_aceitacao` em
  `informacoes_ausentes` (não muda o status da HU). HU ausente da resposta ou lote com falha → lacuna, sem
  derrubar as demais.
- **Pipeline do job de geração:** `preparacao` → `geracao_us` → `consolidacao_us` → `validacao_us` →
  `geracao_criterios` → `validacao_criterios` → `persistencia` → `auditoria_evidencias`. Cada etapa registra `status`
  (`em_andamento | concluida | erro | ignorada`) e `duracao_s` em `resultado.etapas`, gravados a cada início e fim de etapa (a interface mostra a etapa atual) e mantidos quando o job falha. A etapa de
  critérios não é fatal: se falhar, as HU são gravadas sem critérios e com a lacuna. **Por padrão o job gera só as
  US:** `geracao_criterios` e `auditoria_evidencias` ficam `ignorada`, a v1 nasce sem critérios (lacuna "Critérios de
  aceitação ainda não gerados…") e o analista gera critérios (`POST /historias/{id}/criterios/gerar`, ou em lote) e
  a auditoria (`POST /historias/{id}/auditoria-evidencias`) quando quiser, na tela da HU.
  `REQSCRIBA_GENERATION_CRITERIA=true` / `REQSCRIBA_GENERATION_EVIDENCE_AUDIT=true` voltam a executá-las no job
  (nesse caso a v1 já nasce com os critérios).
- **Rastreabilidade (Milestone 2):** cada trecho (`trechos_fonte`) tem id permanente, fonte (`tipo_fonte`,
  `fonte_id`, `nome_arquivo`) e localização; `GET /trechos/{id}` devolve a fonte original e onde o trecho é usado.
  Evidência = cópia imutável do trecho na HU, com `afirmacoes_sustentadas` (perfil, funcionalidade, beneficio).
  Critério → `fontes[{trecho_id, evidencia_id, afirmacoes_sustentadas}]`. Versão → `evidencia_ids` e
  `criterio_ids`. `GET /historias/{id}/rastreabilidade` traz também os critérios, inclusive os substituídos.
- **Validação estrutural** (`GET /historias/{id}/validacao-estrutural`, `GET /projetos/{id}/validacao-estrutural`):
  problemas com `tipo` (`campo_obrigatorio`, `id_duplicado`, `criterio_duplicado`, `referencia_inexistente`,
  `citacao_divergente`, `afirmacao_invalida`, `vinculo_invalido`, `sem_evidencia`), `severidade` (`erro | aviso`),
  `alvo` (`historia | componente | evidencia | criterio | versao`) e `mensagem`. `valida` = nenhum erro.
- **Auditoria semântica** (`### TAREFA: VALIDACAO_EVIDENCIAS`, template `validacao_evidencias.md`): classifica cada
  componente e cada critério como `COMPATIVEL | PARCIALMENTE_COMPATIVEL | INCOMPATIVEL | INSUFICIENTE |
  SEM_EVIDENCIA`, com a avaliação de cada trecho, os problemas, as partes do critério sem sustentação e uma
  correção proposta opcional (só texto). Regras do código: sem trecho → `SEM_EVIDENCIA`; não avaliado ou valor
  desconhecido → `INSUFICIENTE`; trecho fora dos vinculados → problema; resumo contado pelo código. Gravada em
  `auditorias_evidencia`; revisão humana por item em `POST /auditorias-evidencias/{id}/revisoes`. Etapa
  `auditoria_evidencias` ao fim do job de geração (não fatal).
- **Novas fontes (Milestone 3, `### TAREFA: ANALISE_IMPACTO_FONTES`, template `analise_impacto_fontes.md`):** etapas
  `preparacao` → `recuperacao` → `analise_impacto` → `consolidacao` → `persistencia`. Recebe os trechos novos (F),
  as HU candidatas com campos, evidências atuais (E), critérios (H…-CA…) e os trechos novos que as apontaram, a
  análise aprovada e os critérios QUS. Devolve `impactos_identificados` (categoria, história, trechos novos,
  justificativa, evidências com componentes, `alteracao_proposta` em campos, `criterios_afetados` REVISAR/MANTER),
  `novas_historias_propostas` (id temporário, campos, fontes com citação), `conflitos_identificados` e
  `historias_nao_afetadas`. Cada item vira uma sugestão pendente com `tipo` = `confirmacao | complementacao |
  modificacao | contradicao | nova_necessidade`, `versao_base` e `detalhes` (componentes e critérios afetados,
  reclassificação, conflitos, alternativa, sinais de recuperação, id temporário e possíveis duplicidades). O
  resultado do job traz `por_categoria`, fontes analisadas e ignoradas, HU candidatas, analisadas, fora do limite e
  não afetadas, reclassificadas e descartadas. Regras de classificação e aprovação: D44 e D45.
- **Versionamento e aprovação (Milestone 4):** cada versão traz `fontes` (fontes que motivaram a mudança),
  `status_historia`, `evidencia_ids` e `criterio_ids`; a comparação entre versões está em
  `GET /historias/{id}/versoes/comparacao`. Propostas: `pendente | aprovada | rejeitada | cancelada`, com
  `motivo_decisao`; `desatualizada` no detalhe quando a HU mudou depois da proposta; aprovação de proposta
  desatualizada ou de nova necessidade duplicada exige confirmação (`409` até lá). Desaprovar uma HU cancela as
  propostas pendentes dela. Decisão: D46.
- **Critérios e versões (Milestone 5):** cada critério traz `versao_historia`, `situacao` (`valido | desatualizado`),
  `validado_na_versao`, `desatualizado_na_versao` e `motivo_situacao`. Mudança de perfil, funcionalidade ou benefício
  desatualiza os critérios anteriores; confirmar ou regerar resolve. Regeneração por `escopo` ou `criterio_ids`,
  mantendo os válidos; em HU aprovada, gera proposta `revisao_criterios`. Decisão: D47.
- **Critérios sob demanda:** `POST /historias/{id}/criterios/gerar` (síncrono) e
  `POST /projetos/{id}/criterios/gerar` (job `geracao_criterios`; ignora HU aprovadas, desaprovadas e, sem
  `substituir`, as que já têm critérios). Os critérios novos substituem os atuais numa nova versão (origem
  `geracao_criterios_ia`); a anterior é preservada. HU aprovada → `409` (`detalhes.motivo` = `historia_aprovada`).
- **Geração — modo chamada única:** quando o texto de todas as fontes (mais a análise aprovada) cabe em
  `REQSCRIBA_GENERATION_BATCH_CHARS`, uma única chamada recebe tudo, e as passadas de contexto e de
  consolidação não rodam. Para modelos com contexto grande (ex.: Llama 4 Scout), aumente essa variável
  (ex.: `300000`).
- **Geração — modo lotes:** lotes de até 6000 caracteres. Com áudios: cada lote de janelas de transcrição é
  enriquecido com até 4 trechos de documentos recuperados por RAG (filtro pelas fontes selecionadas);
  trechos de documentos não usados são processados em lotes próprios. Deduplicação final por
  similaridade (≥ 0,85) mescla evidências, critérios e funcionalidades de origem (nos dois modos).
- **Análise das fontes** (`services/source_analysis.py`, job `analise_fontes`): roda antes e independente da
  geração (também quando entram novas fontes). Um `AnalysisBuilder` (`services/analysis.py`) é semeado com a
  análise mais recente do projeto (itens, códigos, evidências, edições e ligações com HU); a análise de cada
  lote é mesclada nele: itens do mesmo tipo com o mesmo código da análise anterior ou parecidos (≥ 0,85) viram
  um só (o texto existente é mantido), evidências são unidas e itens novos recebem o próximo `RN`/`FN`. O
  resultado é gravado como NOVA análise (`revisada = 0`); a anterior não muda.
- **Edição bloqueada (`409`):** enquanto houver job `analise_fontes` pendente/processando no projeto, nenhuma
  análise pode ser editada, aprovada, criada ou excluída (`detalhes.motivo = "analise_em_andamento"`). Só a
  análise mais recente aceita edição e aprovação (`"analise_desatualizada"`, com `analise_atual_id`). Na
  inicialização da API, jobs pendentes/processando (órfãos de um reinício) viram `erro`.
- **Aprovação como pré-requisito:** `POST /historias/gerar` exige uma análise com `revisada = 1` (`analise_id`
  informado ou a aprovada mais recente), senão `422`. Ela entra no prompt de histórias como "ANALISE DO
  PROJETO", descontada do orçamento dos lotes; não é fonte e não pode ser citada. Depois da criação das HU,
  cada funcionalidade dessa análise guarda os ids das HU que a citaram em `funcionalidade_ref`.
- **Termos do domínio:** a análise das fontes lista, como itens `termo`, os termos que as fontes usam com
  sentido próprio e o significado dado por elas (`complemento`; `null` quando a fonte não define o termo).
  Na análise aprovada, eles entram na "ANALISE DO PROJETO" como "Termos do domínio".
- **Passada de contexto prévia** (`services/context.py`, `REQSCRIBA_GENERATION_CONTEXT_PASS`, só no modo
  lotes): usada pela análise das fontes e pela análise de novas fontes quando o projeto **não** tem análise
  aprovada. A geração de HU não a usa: a análise aprovada (usuários, requisitos de negócio, funcionalidades e
  termos) já dá a cada lote a visão do todo. O LLM lê as fontes em lotes (`### TAREFA: CONTEXTO_REUNIAO`) e
  lista perfis, objetivos e termos, cada item citando rótulos `F`. Itens sem rótulo válido são descartados;
  itens parecidos (≥ 0,85, mesmo tipo) são mesclados; perfis e objetivos têm prioridade até o limite
  `REQSCRIBA_GENERATION_CONTEXT_CHARS`. Os itens recebem rótulos `R1..Rn` e entram nos prompts dos lotes. Um
  item (de análise ou HU) que cita `R#` recebe como evidência os trechos reais do item (até 3). O orçamento de
  cada lote é reduzido pelo tamanho do contexto (piso: metade). Falhas são registradas e o job segue sem contexto.
- **Passada de consolidação** (`services/consolidation.py`, `REQSCRIBA_GENERATION_CONSOLIDATION`, só com mais de um lote): depois da
  deduplicação, o LLM recebe só as candidatas (`C1..Cn`, sem trechos) e devolve apenas operações
  (`### TAREFA: CONSOLIDACAO_HISTORIAS`): `fundir`, `preencher` (perfil/benefício ausente copiado
  literalmente de outra candidata, com as evidências dela; nunca sobrescreve) e `contradicoes` (lacuna
  `conflito` nas envolvidas; candidatas contraditórias nunca são fundidas). O código aplica as operações;
  falha do LLM mantém as candidatas. O resultado do job de geração traz `consolidacao` (`null` se desligada ou
  em chamada única); o da análise de novas fontes traz também `contexto` (`null` quando não houve passada).
- **Análise de atualização:** para cada HU não desaprovada, busca vetorial restrita às novas fontes
  (top-4) → LLM classifica `sem_relacao|complementa|contradiz` e propõe conteúdo. Trechos novos não
  citados por nenhuma HU passam pela extração → sugestões `novo_requisito` (descartando duplicatas ≥ 0,90).
  Sem análise aprovada, o contexto prévio é construído sobre todas as novas fontes quando elas não cabem
  numa chamada (com análise aprovada, não há passada de contexto), e a
  consolidação roda sobre as candidatas (com mais de um lote) antes do descarte de duplicatas. A análise
  aprovada mais recente entra no prompt como "ANALISE DO PROJETO"; para atualizar a análise com as novas
  fontes, use o job `analise_fontes`.

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
| `ExternalServiceError` (LLM/embeddings/Chroma/ASR) | 502 | `servico_externo` |
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
| `REQSCRIBA_LLM_PROVIDER` | `ollama` (ou `openai_compatible`) |
| `REQSCRIBA_LLM_MODEL` | `llama4:scout` |
| `REQSCRIBA_LLM_BASE_URL` | vazio; obrigatória em `openai_compatible` |
| `REQSCRIBA_LLM_API_KEY` | vazio |
| `REQSCRIBA_LLM_TIMEOUT` | `900` (aceita `REQSCRIBA_OLLAMA_TIMEOUT` como alternativa) |
| `REQSCRIBA_LLM_TEMPERATURE` / `REQSCRIBA_LLM_NUM_CTX` | `0.1` / `16384` (`NUM_CTX` só no Ollama) |
| `REQSCRIBA_OLLAMA_URL` | `http://localhost:11434` (usada se o Ollama não tiver URL própria) |
| `REQSCRIBA_EMBEDDINGS_PROVIDER` | `ollama` (ou `openai_compatible`; `hash` para modo offline) |
| `REQSCRIBA_EMBEDDING_MODEL` | `bge-m3` |
| `REQSCRIBA_EMBEDDINGS_BASE_URL` / `REQSCRIBA_EMBEDDINGS_API_KEY` | vazios; sem URL própria e com o mesmo provedor, herdam URL e chave do LLM |
| `REQSCRIBA_VECTOR_STORE` | `chroma` (`memory` para testes) |
| `REQSCRIBA_TRANSCRIPTION_PROVIDER` | `faster_whisper` (local) ou `openai_compatible` (servidor HTTP, ex.: repositório `req-scriba-asr`) |
| `REQSCRIBA_TRANSCRIPTION_BASE_URL` / `REQSCRIBA_TRANSCRIPTION_API_KEY` | vazios; URL obrigatória em `openai_compatible` (ex.: `http://localhost:9001/v1`) |
| `REQSCRIBA_TRANSCRIPTION_TIMEOUT` | `1800` (segundos, só no remoto) |
| `REQSCRIBA_WHISPER_MODEL` / `_DEVICE` / `_COMPUTE_TYPE` | `small` / `cpu` / `int8` (no remoto, só o modelo vale: nome listado pelo servidor) |
| `REQSCRIBA_TRANSCRIPTION_LANGUAGE` | `pt` |
| `REQSCRIBA_MAX_AUDIO_MB` / `REQSCRIBA_MAX_DOCUMENT_MB` | `500` / `20` |
| `REQSCRIBA_CHUNK_SIZE` / `REQSCRIBA_CHUNK_OVERLAP` | `1000` / `150` |
| `REQSCRIBA_TRANSCRIPT_WINDOW_CHARS` | `800` |
| `REQSCRIBA_GENERATION_BATCH_CHARS` | `6000` (fontes até esse tamanho vão numa chamada única; para modelos de contexto grande, ex. `300000`) |
| `REQSCRIBA_RAG_TOP_K` | `4` |
| `REQSCRIBA_GENERATION_CONTEXT_PASS` | `true` (passada de contexto prévia: análise das fontes e novas fontes sem análise aprovada) |
| `REQSCRIBA_GENERATION_CONTEXT_CHARS` | `1200` (limite do bloco de contexto nos prompts) |
| `REQSCRIBA_GENERATION_CONSOLIDATION` | `true` (passada de consolidação) |
| `REQSCRIBA_GENERATION_CRITERIA` | `false` (etapa de critérios de aceitação dentro do job de geração; padrão: sob demanda na tela da HU) |
| `REQSCRIBA_CRITERIA_RELATED_CHUNKS` | `2` (trechos relacionados por HU no prompt de critérios; `0` desliga) |
| `REQSCRIBA_GENERATION_EVIDENCE_AUDIT` | `false` (auditoria semântica das evidências ao fim do job de geração; padrão: sob demanda na tela da HU) |
| `REQSCRIBA_IMPACT_TOP_STORIES` | `3` (HU mais próximas por embeddings para cada trecho novo; `0` desliga o sinal) |
| `REQSCRIBA_IMPACT_MAX_STORIES` | `12` (HU candidatas por chamada de ANALISE_IMPACTO_FONTES) |
| `REQSCRIBA_IMPACT_EVIDENCES_PER_STORY` | `3` (evidências atuais de cada HU enviadas ao prompt) |
| `REQSCRIBA_LLM_STRUCTURED_OUTPUT` | `false` (envia o JSON Schema do prompt ao provedor) |
| `REQSCRIBA_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` |
| `REQSCRIBA_LOG_LEVEL` | `INFO` |

## 14. Observabilidade

Logging estruturado por módulo (`logging`), incluindo duração das chamadas ao LLM e ao ASR, ids de
job/fonte. Conteúdo de transcrições e documentos **não** é logado (apenas tamanhos e ids).
`GET /api/v1/health` verifica SQLite, ChromaDB (e reindexação pendente), LLM e embeddings (provedor e
modelos disponíveis, em `componentes.llm` e `componentes.embeddings`) e disponibilidade do ASR.
