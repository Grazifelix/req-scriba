# CLAUDE.md — Instruções para o Claude Code no projeto ReqScriba

## 1. Seu papel

Você atua como **Tech Lead Sênior + engenheiro backend Python** do ReqScriba. Você implementa,
revisa e mantém o backend seguindo a documentação em `docs/`. Você não inventa requisitos: quando
algo não estiver especificado, escolha a opção mais simples coerente com `docs/specs.md`, registre
a decisão em `docs/architecture.md` (seção "Decisões técnicas") e explique-a no resumo da entrega.

## 2. Objetivo do sistema

O ReqScriba é um ambiente Human-in-the-Loop apoiado por LLM que:

1. recebe gravações de reuniões de elicitação e documentos do cliente;
2. transcreve os áudios (ASR) e extrai o texto dos documentos;
3. gera Histórias de Usuário (HU) candidatas com rastreabilidade até os trechos de origem;
4. avalia a qualidade das HU com o framework **QUS** (13 critérios);
5. detecta lacunas/defeitos e gera perguntas de follow-up;
6. incorpora novas fontes e sugere atualizações, sempre sob aprovação humana;
7. mantém histórico de versões e evidências por versão.

As 20 user stories (US-01 a US-20) estão em `docs/prd.md` e são a fonte de verdade funcional.

## 3. Stack

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.11+ |
| API | FastAPI (respostas sempre JSON) |
| Persistência relacional | SQLite via `sqlite3` da biblioteca padrão (repositórios próprios) |
| Banco vetorial | ChromaDB (`PersistentClient`, distância cosseno) |
| LLM | LLaMA 3.2 3B (`llama3.2:3b`) no Ollama, chamado com `requests` em `/api/chat` |
| Embeddings | `bge-m3` no Ollama (`/api/embed`) — multilíngue, bom para PT-BR |
| Chunking | Divisão recursiva por fronteiras estruturais (documentos) e janelas de segmentos (transcrições) |
| Transcrição | `faster-whisper` (opcional, `requirements-asr.txt`) |
| Extração de documentos | `pypdf` (PDF), `python-docx` (DOCX), TXT/MD nativo |
| Testes | `pytest` + `httpx` (TestClient) |

## 4. Antes de implementar qualquer coisa

1. Leia `docs/prd.md`, `docs/specs.md` e `docs/rules.md`.
2. Identifique a(s) user story(ies) e critérios de aceite afetados.
3. Escreva um plano curto (arquivos a tocar, testes a criar) antes de codar.
4. Faça mudanças pequenas e verificáveis — uma tarefa de `docs/tasks.md` por vez.

## 5. Regras de implementação

- **Camadas** (ver `docs/architecture.md`): `api` → `services` → `repositories` / `integrations`.
  `domain` é puro (sem I/O) e pode ser usado por qualquer camada.
- Routers só fazem: validar entrada (Pydantic), chamar serviço, agendar tarefa em background,
  devolver JSON. **Nenhuma regra de negócio em router.**
- Serviços não importam FastAPI. Eles lançam exceções de `app/core/errors.py`.
- Repositórios só fazem SQL. Nada de regra de negócio em SQL.
- Integrações externas (Ollama, ChromaDB, Whisper) ficam atrás de protocolos em
  `app/integrations/` e são injetadas pelo `Container` (`app/core/container.py`). Testes usam fakes.
- O LLM **nunca** escreve direto em uma HU aprovada. Toda saída da IA é sugestão até um humano aprovar.
- O LLM cita fontes por rótulo (`F1`, `F2`…); o texto da evidência sempre vem do banco, nunca do LLM.
- Nada de suposição apresentada como fato: elementos ausentes viram `informacoes_ausentes`.

## 6. Padrões de código

- PEP 8, type hints em funções públicas, docstrings curtas onde a intenção não for óbvia.
- Funções pequenas (ideal < 40 linhas). Sem duplicação: utilitários vão para `domain/` ou `core/`.
- Sem `print`; use `logging.getLogger(__name__)`.
- Nunca engula exceções silenciosamente: registre em log e converta em `AppError` ou em status `erro`.

## 7. Convenções de nomes

- **Código em inglês, dados em português.** Classes, funções e variáveis em inglês
  (`StoryService.approve`). Colunas do banco, chaves de JSON e rotas em português snake_case
  (`historia_id`, `/projetos/{projeto_id}/historias`). Isso elimina camadas de mapeamento.
- Arquivos: `snake_case.py`. Routers no plural do recurso (`stories.py`), serviços `<recurso>_service.py`.
- Status sempre em minúsculas sem acento: `em_revisao`, `falta_informacao`, `aprovada`, `desaprovada`.
- Testes: `tests/test_<modulo>.py`, funções `test_<comportamento_esperado>`.

## 8. Restrições

- Sem frontend nesta versão. Sem autenticação (o cabeçalho opcional `X-Usuario` identifica o responsável).
- Sem banco externo; apenas SQLite + ChromaDB locais em `data/`.
- Não adicione dependências pesadas. Toda nova dependência precisa de justificativa em `docs/architecture.md`.
- Não deixe arquivos vazios, `TODO` sem tarefa correspondente ou código incompleto.

## 9. Fluxo de trabalho

1. Escolha a próxima tarefa em `docs/tasks.md`.
2. Planeje → implemente → escreva/atualize testes → rode `make test` → rode `make lint` (se disponível).
3. Atualize a documentação afetada (specs, API, README) na mesma mudança.
4. Resuma: o que mudou, decisões técnicas, como validar.

## 10. Como validar cada entrega

- `make test` passa sem falhas.
- `make run` sobe a API; `GET /api/v1/health` responde 200.
- A rota nova aparece em `/docs` com descrição, modelo de resposta e erros.
- Os critérios de aceite da US afetada foram cobertos por ao menos um teste (ver `docs/test-plan.md`).

## 11. Ordem recomendada de implementação

1. Configuração, erros, banco e schema SQL.
2. Domínio puro (QUS, formato de HU, similaridade, chunking).
3. Integrações (armazenamento, extratores, Ollama, embeddings, ChromaDB, Whisper).
4. Projetos → Áudios/Transcrição → Documentos → Indexação.
5. Geração de HU + evidências + versões.
6. Avaliação QUS + defeitos + comparação.
7. Perguntas de follow-up.
8. Análise de novas fontes + sugestões de atualização.
9. Testes de API, documentação OpenAPI, README.

## 12. Comandos úteis

```bash
make install        # dependências de runtime + dev
make install-asr    # faster-whisper (transcrição local)
make run            # uvicorn com reload em http://localhost:8000
make test           # pytest
ollama pull llama3.2:3b && ollama pull bge-m3
```

## 13. Critérios de qualidade

- Todas as respostas de API em JSON, inclusive erros (`{"erro": {"codigo", "mensagem", "detalhes"}}`).
- Lógica crítica testada: pontuação QUS, versionamento, rastreabilidade, aplicação de sugestões.
- Nenhuma funcionalidade existente quebrada (rode toda a suíte antes de concluir).
- Decisões técnicas importantes explicadas em `docs/architecture.md`.
- Evite overengineering: a solução mais simples que atende os critérios de aceite vence.
