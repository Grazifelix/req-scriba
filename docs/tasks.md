# Plano de Implementação

Status: ✅ concluída nesta versão · ⏳ depende de ambiente do usuário (ver nota).

## Etapa 1 — Setup e configuração

### T1.1 Estrutura do projeto e dependências ✅
- **Descrição:** criar pastas, `requirements.txt`, `requirements-dev.txt`, `requirements-asr.txt`, `Makefile`, `.env.example`, `.gitignore`.
- **Arquivos:** raiz do projeto.
- **Critério de aceite:** `make install` instala tudo; `make run` sobe a API.
- **Dependências:** —

### T1.2 Configuração e erros ✅
- **Descrição:** `Settings` lendo `REQSCRIBA_*`; hierarquia `AppError`; utilitário de tempo.
- **Arquivos:** `app/core/config.py`, `app/core/errors.py`, `app/core/time.py`.
- **Critério de aceite:** erros convertidos no formato `{"erro": {...}}`; config com padrões documentados.
- **Dependências:** T1.1

## Etapa 2 — Persistência local

### T2.1 Schema SQLite e UnitOfWork ✅
- **Descrição:** DDL de todas as tabelas, PRAGMAs, transação por requisição.
- **Arquivos:** `app/db/schema.sql`, `app/db/database.py`, `app/db/unit_of_work.py`.
- **Critério de aceite:** banco criado na inicialização; rollback em exceção; FKs ativas.
- **Dependências:** T1.2

### T2.2 Repositórios ✅
- **Descrição:** CRUD SQL por agregado com (de)serialização JSON.
- **Arquivos:** `app/repositories/{base,projects,sources,stories,workflow}.py`.
- **Critério de aceite:** testes de repositório passam com banco temporário.
- **Dependências:** T2.1

## Etapa 3 — Domínio

### T3.1 QUS, formato de HU, similaridade, chunking ✅
- **Arquivos:** `app/domain/*.py`.
- **Critério de aceite:** tabela de porcentagens 1–13 e faixas exatas; template com `[A DEFINIR]`; chunking com offsets corretos.
- **Dependências:** T1.1

## Etapa 4 — Integrações

### T4.1 Armazenamento e extratores ✅
- **Arquivos:** `app/integrations/storage.py`, `app/integrations/extractors.py`.
- **Critério de aceite:** PDF/DOCX/TXT extraídos por página; nomes saneados; limite de tamanho.
- **Dependências:** T1.2

### T4.2 Ollama (LLM + embeddings) ✅
- **Arquivos:** `app/integrations/ollama.py`, `app/integrations/embeddings.py`, `app/llm/*.py`.
- **Critério de aceite:** chat JSON com retentativa; erros viram `ExternalServiceError`; testes com `requests` simulado.
- **Dependências:** T1.2

### T4.3 ChromaDB e Whisper ✅ (⏳ execução real requer `chromadb`/`faster-whisper` instalados)
- **Arquivos:** `app/integrations/vector_store.py`, `app/integrations/transcriber.py`.
- **Critério de aceite:** upsert/consulta/remoção com filtro por projeto e fonte; transcrição com segmentos e tempos.
- **Dependências:** T4.2

### T4.4 Container de dependências ✅
- **Arquivos:** `app/core/container.py`.
- **Critério de aceite:** testes constroem o container com fakes sem rede.
- **Dependências:** T4.1–T4.3

## Etapa 5 — Fontes

### T5.1 Projetos ✅ — `services/projects.py`, `api/routers/projects.py`. Aceite: CRUD + totais + qualidade geral. Dep.: T2.2
### T5.2 Áudios e transcrição (US-01..03) ✅ — `services/audios.py`, `services/indexing.py`, `workers/tasks.py`, `api/routers/sources.py`. Aceite: CAs de US-01..03. Dep.: T5.1, T4.3
### T5.3 Documentos (US-04) ✅ — `services/documents.py`, `api/routers/sources.py`. Aceite: CAs de US-04. Dep.: T5.1, T4.1
### T5.4 Busca semântica ✅ — `services/retrieval.py`, `api/routers/projects.py` (rota `/busca`). Aceite: resultados filtrados por projeto. Dep.: T5.2, T5.3

## Etapa 6 — Histórias, evidências e versões

### T6.1 Versionamento e evidências (US-16..20) ✅ — `services/versioning.py`, `services/evidence.py`. Aceite: versão inicial, nova versão por alteração, evidências por versão. Dep.: T2.2
### T6.2 Geração (US-05, US-16) ✅ — `services/generation.py`, `llm/prompts.py`. Aceite: HU padronizadas, lacunas, evidências, deduplicação. Dep.: T6.1, T5.4
### T6.3 Revisão (US-06) ✅ — `services/stories.py`, `api/routers/stories.py`. Aceite: editar/aprovar/desaprovar; backlog correto. Dep.: T6.1

## Etapa 7 — Qualidade

### T7.1 Avaliação QUS, defeitos e comparação (US-07..09) ✅ — `services/quality.py`, `api/routers/workflow.py`. Aceite: 13 critérios, histórico, comparação, visão geral. Dep.: T6.3

## Etapa 8 — Follow-up

### T8.1 Perguntas (US-10..12) ✅ — `services/questions.py`, `api/routers/workflow.py`. Aceite: perguntas por defeito, limitações, revisão, agrupamento, respostas. Dep.: T7.1

## Etapa 9 — Evolução iterativa

### T9.1 Análise de novas fontes e sugestões (US-13..15) ✅ — `services/updates.py`, `api/routers/workflow.py`. Aceite: complementa/contradiz/novo; diff; aplicação gera versão; rejeição preserva. Dep.: T6.2, T7.1

## Etapa 10 — Testes

### T10.1 Testes unitários e de serviço ✅ — `tests/test_domain_*.py`, `tests/test_llm_parsing.py`, `tests/test_integrations.py`, `tests/test_services_*.py`, `tests/test_schemas.py`. Dep.: todas
### T10.2 Testes de API ✅ (⏳ executam quando `fastapi`/`httpx` estão instalados) — `tests/test_api.py`. Dep.: todas

## Etapa 11 — Polimento e documentação final

### T11.1 OpenAPI completo ✅ — summaries, descriptions, exemplos, respostas de erro em todos os routers.
### T11.2 README e docs revisados ✅ — instalação do zero, Ollama, Whisper, exemplos `curl`.
### T11.3 Preparação para frontend em projeto separado ✅ — `app/main.py`, `tests/test_api.py`, `docs/ux-guidelines.md` §8. Aceite: erro 500 com CORS e JSON; testes de CORS; guia de integração. Dep.: T11.1
### T11.4 Arquitetura agnóstica de LLM ✅ — `app/integrations/{llm,ollama,openai_compatible,embeddings,vector_store}.py`, `app/core/{config,container}.py`, `app/services/indexing.py`, `app/workers/reindex.py`. Aceite: provedores por configuração; coleção por modelo; `make reindex`; `/health` neutro. Dep.: T4.2, T4.3

## Backlog técnico (próximas versões)
- Registrar `modelo_llm` e `versao_prompt` em versões e avaliações (exige migração do banco).
- Fila dedicada (RQ/Arq) e recuperação de jobs interrompidos na inicialização.
- Migrações versionadas (ex.: `yoyo-migrations`) ao migrar para PostgreSQL.
- Autenticação JWT e escopo de projetos por usuário.
- Streaming de progresso (SSE) para jobs longos.
