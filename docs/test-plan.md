# Plano de Testes — ReqScriba Backend v1

## 1. Estratégia

| Nível | Ferramenta | Escopo | Dependências externas |
|---|---|---|---|
| Unitário | pytest | `domain/`, `llm/parsing.py`, extratores, cliente Ollama | nenhuma (requests simulado) |
| Componente (serviço) | pytest + SQLite temporário | `services/` + `repositories/` + `workers/` | fakes: `FakeLLM`, `FakeTranscriber`, `HashEmbedder`, `InMemoryVectorStore` |
| Integração (API) | pytest + `fastapi.testclient` | rotas, status HTTP, contrato JSON, OpenAPI | fakes (mesmo container) |
| Adaptadores reais | pytest (marcados, opcionais) | ChromaDB persistente | `chromadb` instalado |
| Manual | Swagger `/docs` + Ollama real | fluxo ponta a ponta | Ollama, Whisper |

Comando: `make test` (equivale a `pytest -q`). Os testes de API e de Chroma usam
`pytest.importorskip`, então a suíte de domínio/serviços roda mesmo sem essas bibliotecas.

## 2. Testes unitários

| Arquivo | Casos |
|---|---|
| `test_domain_qus.py` | porcentagem para 0..13 critérios (7,69 … 100,00); faixas nos limites (24,99/25/49,99/50/74,99/75/99,99/100); normalização de resultados ("atende", "Parcial", "não atende", desconhecido) |
| `test_domain_story_format.py` | montagem do template; `[A DEFINIR: …]` para elementos ausentes; parsing de texto livre "Como…, quero…, para…"; texto fora do padrão |
| `test_domain_text_chunking.py` | normalização; similaridade; offsets dos chunks batem com o texto; sobreposição; janelas de segmentos respeitam limite e tempos |
| `test_llm_parsing.py` | extração tolera chaves alternativas, descarta itens sem funcionalidade, rótulos inválidos; avaliação QUS preenche critérios faltantes com `Atenção`; JSON envolvido em texto/markdown |
| `test_integrations.py` | `OllamaClient` (sucesso, JSON inválido + retentativa, timeout → `ExternalServiceError`); extratores TXT/DOCX/PDF; saneamento de nome; assinatura binária |

## 3. Testes de componentes (serviços)

| Arquivo | US/CA cobertos |
|---|---|
| `test_services_sources.py` | US-01 CA01–03 (upload, rejeição com motivo, estados); US-02 CA01–03 (segmentos com tempo, falha + reprocessar); US-03 CA01–04 (busca, correção usada nas janelas/índice, `url_momento`); US-04 CA01–03 (documento sem texto → erro, não indexado) |
| `test_services_stories.py` | US-05 CA01–04; US-06 CA01–04; US-16 CA01–04; US-17 CA01–04; US-19 CA01–04; US-20 CA01–03; regra RN11 (fonte com erro bloqueia geração) |
| `test_services_quality.py` | US-07 CA01–03; US-08 CA01–02 (histórico preservado, comparação resolvido/novo); US-09 CA01–03 (lacunas sem invenção); regras determinísticas (well_formed, unique, uniform) |
| `test_services_questions.py` | US-10 CA01–04; US-11 CA01–03; US-12 CA01–03 |
| `test_services_updates.py` | US-13 CA01–03; US-14 CA01–04 (HU aprovada intocada até aprovar); US-15 CA01–04; US-18 CA01–03 |
| `test_schemas.py` | Contrato: todas as respostas dos serviços validam contra os `response_model` da API (roda sem FastAPI) |

## 4. Testes de integração (API)

`test_api.py` (executa com FastAPI instalado):
- fluxo completo: projeto → upload áudio (`202`) → transcrição → upload documento → gerar (job) →
  HU → avaliar → perguntas → aprovar → nova fonte → analisar → aprovar sugestão → versões;
- contrato de erro: 404 de recurso, 404 de rota, 422 de Pydantic (`entrada_invalida`), 422 de negócio,
  409 de conflito — todos com `{"erro": {...}}`;
- listas no formato `{"itens","total"}`;
- `/openapi.json` contém todas as rotas documentadas em `architecture.md`;
- `GET /audios/{id}/arquivo` devolve o binário com `content-type` correto.

## 5. Casos críticos

1. Pontuação QUS e faixas exatamente como a tabela do PRD.
2. HU aprovada não muda ao gerar sugestão; muda somente após `aprovar` sugestão.
3. Rejeitar sugestão preserva conteúdo e versão.
4. Cada alteração de conteúdo cria exatamente uma versão nova; aprovar/desaprovar não cria versão.
5. Evidências de versões anteriores continuam acessíveis após atualização.
6. Correção de transcrição altera os trechos usados na próxima geração.
7. LLM citando rótulo inexistente não cria evidência; HU sem evidência é sinalizada.
8. Documento sem texto extraível nunca é usado na geração.

## 6. Casos de erro

| Caso | Esperado |
|---|---|
| Upload com extensão não suportada | 422 `validacao`, mensagem com extensões aceitas |
| Upload vazio / acima do limite / assinatura inconsistente | 422 com motivo específico |
| Projeto/HU/pergunta inexistente | 404 `nao_encontrado` |
| Gerar HU sem fontes ou com fonte de outro projeto / não concluída | 422 |
| Aprovar sugestão já decidida | 409 `conflito` |
| Comparar avaliações com menos de duas | 409 |
| Ollama fora do ar na avaliação | 502 `servico_externo` |
| Ollama fora do ar em job | job `erro` com `mensagem_erro` |
| Whisper não instalado | áudio `erro` com instrução de instalação; `reprocessar` disponível |
| JSON malformado do LLM | retentativa; persistindo, erro explícito |

## 7. Testes de acessibilidade

Sem UI na v1; a acessibilidade é garantida no **contrato** para o frontend futuro:
- todo status possui `status_rotulo` legível em português (leitores de tela não leem `em_revisao`);
- mensagens de erro em linguagem clara, sem jargão técnico, indicando como corrigir;
- tempos fornecidos em segundos **e** formatados (`"00:01:02"`) para exibição;
- teste automatizado verifica que todos os status expostos têm rótulo (`test_domain_text_chunking.py::test_all_status_have_labels`).

## 8. Testes manuais (roteiro com Ollama real)

1. `ollama pull llama3.2:3b && ollama pull bge-m3`; `make install-asr`; `make run`.
2. `GET /api/v1/health` → todos os componentes `ok`.
3. Criar projeto; enviar um `.mp3` curto em PT-BR; acompanhar `status` até `concluido`.
4. Abrir `/audios/{id}/transcricao`, buscar um termo, corrigir um segmento.
5. Enviar um PDF com texto e um PDF digitalizado (imagem) → o segundo deve ir para `erro`.
6. Gerar HU com ambas as fontes; conferir evidências com tempos e páginas.
7. Avaliar uma HU; editar seguindo `acao`; reavaliar; comparar.
8. Gerar perguntas; editar uma, rejeitar outra, aprovar as demais; ver `/followup?agrupar_por=tipo_defeito`.
9. Registrar respostas; enviar nova gravação; analisar atualizações; aprovar uma editando o texto; rejeitar outra.
10. Conferir `/historias/{id}/versoes` e `/rastreabilidade`.
11. Reiniciar a API e confirmar que tudo persiste (SQLite + Chroma).
