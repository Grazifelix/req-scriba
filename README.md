# ReqScriba

Gerador e analisador de **Histórias de Usuário** apoiado por LLM, com arquitetura
**Human-in-the-Loop**. Transforma gravações de reuniões de elicitação e documentos do cliente em um
backlog rastreável, avaliado pelo framework **Quality User Story (QUS)**, e conduz rodadas
iterativas de esclarecimento com os stakeholders. Tudo roda localmente.

Este repositório contém o **backend e a API REST**. O frontend é um projeto separado, que se conecta
por HTTP: veja o [guia de integração](docs/ux-guidelines.md#8-integração-do-frontend).

## Stack

| Camada | Tecnologia |
|---|---|
| API | Python 3.11+ · FastAPI (JSON) |
| Persistência | SQLite (`sqlite3` da stdlib) |
| Banco vetorial | ChromaDB (persistente, cosseno) |
| LLM | Qwen 3.5 4B no Ollama, via `requests` (`/api/chat`, `format: json`, `think: false`) |
| Embeddings | `bge-m3` no Ollama (`/api/embed`) |
| Chunking | Divisão recursiva por fronteiras estruturais (docs) · janelas de segmentos (transcrições) |
| Transcrição | faster-whisper (opcional) |
| Documentos | pypdf · python-docx · TXT/MD |

## Funcionalidades

- **Fontes:** upload de áudio (mp3, wav, m4a, ogg, flac, webm, mp4) e documentos (pdf, docx, txt, md),
  com validação, estados (`pendente → processando → concluido | erro`) e reprocessamento.
- **Transcrição:** segmentos com tempos, busca sem acento/maiúsculas, correção que alimenta as
  próximas extrações e link para o momento exato do áudio.
- **Geração de HU:** "Como [perfil], quero [funcionalidade], para [benefício]", com
  `[A DEFINIR: …]` quando falta informação, critérios de aceitação e evidências (trecho + tempo/página).
- **Revisão humana:** editar, aprovar, desaprovar; backlog aprovado separado.
- **Qualidade QUS:** 13 critérios (8 individuais + 5 de conjunto), porcentagem e faixa, histórico,
  comparação entre avaliações, defeitos e lacunas, visão geral do projeto.
- **Follow-up:** perguntas por defeito com justificativa, limitações explícitas, revisão,
  agrupamento por história ou tipo de defeito e registro de respostas.
- **Evolução iterativa:** novas fontes analisadas via RAG → sugestões (complementa, contradiz,
  novo requisito) com comparação atual × proposto; nada muda sem aprovação.
- **Rastreabilidade e versões:** evidências por versão, origem manual/IA, responsável e data.

## Como instalar

Pré-requisitos: Python 3.11+ e [Ollama](https://ollama.com). Para transcrever, instale o
faster-whisper (`make install-asr`), que decodifica o áudio via PyAV sem exigir `ffmpeg` instalado.

```bash
git clone <repo> reqscriba && cd reqscriba
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
make install          # ou: pip install -r requirements-dev.txt
make install-asr      # opcional: transcrição local (faster-whisper)
cp .env.example .env  # ajuste se necessário

ollama pull qwen3.5:4b
ollama pull bge-m3
```

## Como rodar

```bash
ollama serve          # se ainda não estiver rodando
make run              # http://localhost:8000
```

- Documentação interativa: <http://localhost:8000/docs> (Swagger) e `/redoc`
- Saúde: `GET http://localhost:8000/api/v1/health`

Exemplo rápido com `curl`:

```bash
API=http://localhost:8000/api/v1
curl -s -X POST $API/projetos -H 'Content-Type: application/json' -d '{"titulo":"Sistema Escolar"}'
curl -s -X POST $API/projetos/1/audios -F arquivo=@reuniao1.mp3
curl -s $API/audios/1                                   # acompanhe o status
curl -s -X POST $API/projetos/1/documentos -F arquivo=@escopo.pdf
curl -s -X POST $API/projetos/1/historias/gerar -H 'Content-Type: application/json' \
     -H 'X-Usuario: ana' -d '{"audio_ids":[1],"documento_ids":[1]}'
curl -s $API/jobs/<job_id>                              # historias_criadas
curl -s -X POST $API/historias/1/avaliar                # QUS
curl -s -X POST $API/historias/1/perguntas/gerar
```

Sem modelo de embeddings? Use `REQSCRIBA_EMBEDDINGS_PROVIDER=hash` (busca semântica de menor qualidade).

## Como testar

```bash
make test             # pytest -q
```

A suíte usa fakes para LLM, ASR, embeddings e banco vetorial — não precisa de Ollama nem Whisper.
Os testes de API (`tests/test_api.py`) e do ChromaDB real são ignorados automaticamente se
`fastapi`/`httpx`/`chromadb` não estiverem instalados.

## Estrutura de pastas

```
app/
├── main.py              # create_app(): FastAPI, CORS, cabeçalhos de segurança, erros JSON
├── core/                # config, erros, container de dependências, tempo
├── db/                  # schema.sql, conexão SQLite, UnitOfWork
├── repositories/        # SQL por agregado
├── domain/              # regras puras: QUS, formato de HU, similaridade, chunking, enums
├── integrations/        # Ollama, embeddings, ChromaDB, Whisper, extratores, armazenamento
├── llm/                 # prompts e normalização de respostas
├── services/            # casos de uso + presenters (JSON)
├── workers/             # tarefas de background
├── schemas/             # modelos Pydantic (OpenAPI)
└── api/                 # dependências e routers
docs/                    # PRD, specs, regras, arquitetura, tarefas, testes, UX, segurança
tests/                   # pytest + fakes
```

Detalhes em [`docs/architecture.md`](docs/architecture.md) e [`docs/specs.md`](docs/specs.md).

## Roadmap

- [x] v1 backend: US-01 a US-20, API documentada, testes
- [ ] Autenticação (JWT) e papéis por projeto
- [ ] Fila dedicada para jobs e retomada após reinício
- [ ] Diarização de falantes e OCR de PDFs digitalizados
- [ ] Exportação (CSV, Jira) e painel de métricas da pesquisa

## Licença

MIT — veja o arquivo [`LICENSE`](LICENSE).
