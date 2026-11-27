# Arquitetura — ReqScriba Backend v1

## 1. Visão da arquitetura

Monólito modular em camadas, local por padrão, orientado a um fluxo Human-in-the-Loop:

```
           ┌───────── Clientes (frontend em projeto separado, Postman, scripts) ─────────┐
                                         │ HTTP/JSON
┌─────────────────────────────────────── FastAPI ─────────────────────────────────────────┐
│  api/routers  →  schemas (Pydantic)  →  api/deps (Container, UnitOfWork, X-Usuario)       │
├─────────────────────────────────────── services ────────────────────────────────────────┤
│ projects · audios · documents · indexing · retrieval · generation · stories · versioning │
│ evidence · analysis · quality · questions · updates · jobs · presenters                   │
├──────────────── domain (puro) ───────────────┬──────────── integrations ─────────────────┤
│ qus · story_format · text · chunking · enums │ llm · ollama · openai_compatible            │
│                                              │ embeddings · vector_store · transcriber     │
│                                              │ extractors · storage                        │
├──────────────── repositories (SQL) ──────────┼─────────────────────────────────────────────┤
│ SQLite (data/reqscriba.db)                   │ ChromaDB (data/chroma) · provedor LLM · disco │
└──────────────────────────────────────────────┴─────────────────────────────────────────────┘
  workers/tasks.py — tarefas de background (BackgroundTasks) com UnitOfWork própria
```

## 2. Camadas do sistema

| Camada | Responsabilidade | Pode depender de |
|---|---|---|
| `api` | HTTP: rotas, validação Pydantic, códigos de status, agendamento de tarefas | services, schemas, core |
| `schemas` | Contratos de entrada/saída (OpenAPI) | pydantic |
| `services` | Casos de uso e regras de negócio, orquestração de LLM/RAG, presenters | domain, repositories (via UoW), integrations (via Container), llm |
| `domain` | Regras puras: QUS, formato de HU, similaridade, chunking, enums | nada (stdlib) |
| `llm` | Prompts e normalização das respostas | domain |
| `repositories` | SQL parametrizado; converte linhas ↔ dict (JSON decodificado) | sqlite3 |
| `integrations` | Adaptadores de serviços externos atrás de protocolos | bibliotecas externas |
| `workers` | Execução assíncrona dos casos de uso longos | services, core |
| `core` | Config, erros, container, tempo | stdlib, pydantic |

### 2.1 Diagrama de classes

Classes do código por camada. Integrações externas ficam atrás de protocolos (`<|..`), e o
`Container` injeta a implementação real ou o fake de teste. Parte da lógica de serviços está em
módulos de funções (`indexing`, `retrieval`, `generation`, `evidence`, `versioning`, `presenters`) e
as tarefas de background são funções (`transcribe_audio`, `process_document`, `run_generation`,
`run_update_analysis`).

```mermaid
classDiagram
    direction TB

    %% ---------- core ----------
    class Settings {
        +llm_provider
        +llm_model
        +embeddings_provider
        +embedding_model
        +whisper_model
        +db_path()
        +chroma_dir()
        +upload_dir()
        +ensure_dirs()
    }
    class Container {
        +settings: Settings
        +db: Database
        +storage: FileStorage
        +llm: LLMClient
        +embedder: Embedder
        +vector_store: VectorStore
        +transcriber: Transcriber
        +uow() UnitOfWork
    }
    class AppError {
        +codigo
        +mensagem
        +detalhes
        +to_dict()
    }
    AppError <|-- NotFoundError
    AppError <|-- BusinessValidationError
    AppError <|-- ConflictError
    AppError <|-- ExternalServiceError

    %% ---------- db / repositories ----------
    class Database {
        +connect()
        +initialize()
        +ping()
    }
    class UnitOfWork {
        +projects, audios, segments, documents, chunks
        +stories, versions, evidences
        +evaluations, questions, suggestions, jobs
        +analyses, analysis_items
        +commit()
        +rollback()
        +close()
    }
    class BaseRepository {
        +insert(values) int
        +update(id, values)
        +get(id) dict
        +delete(id)
    }
    BaseRepository <|-- ProjectRepository
    BaseRepository <|-- AudioRepository
    BaseRepository <|-- SegmentRepository
    BaseRepository <|-- DocumentRepository
    BaseRepository <|-- SourceChunkRepository
    BaseRepository <|-- StoryRepository
    BaseRepository <|-- VersionRepository
    BaseRepository <|-- EvidenceRepository
    BaseRepository <|-- EvaluationRepository
    BaseRepository <|-- QuestionRepository
    BaseRepository <|-- UpdateSuggestionRepository
    BaseRepository <|-- JobRepository
    BaseRepository <|-- AnalysisRepository
    BaseRepository <|-- AnalysisItemRepository
    UnitOfWork *-- BaseRepository : 14 repositórios
    UnitOfWork --> Database

    %% ---------- integrations ----------
    class LLMClient {
        <<protocol>>
        +generate_json(system, prompt) dict
        +health()
    }
    class Embedder {
        <<protocol>>
        +embed(texts) list
        +health()
    }
    class VectorStore {
        <<protocol>>
        +upsert()
        +delete()
        +delete_where()
        +query(embedding, top_k, where)
        +health()
    }
    class Transcriber {
        <<protocol>>
        +transcribe(path) TranscriptionResult
        +health()
    }
    class OllamaClient {
        +generate_json()
        +embed()
        +health()
        +embedding_health()
    }
    class OpenAICompatibleClient {
        +generate_json()
        +embed()
        +health()
        +embedding_health()
    }
    class FileStorage {
        +save()
        +resolve()
        +remove()
    }
    LLMClient <|.. OllamaClient
    LLMClient <|.. OpenAICompatibleClient
    Embedder <|.. RemoteEmbedder
    Embedder <|.. HashEmbedder
    RemoteEmbedder --> OllamaClient : /api/embed
    RemoteEmbedder --> OpenAICompatibleClient : /embeddings
    VectorStore <|.. ChromaVectorStore
    VectorStore <|.. InMemoryVectorStore
    Transcriber <|.. FasterWhisperTranscriber
    FasterWhisperTranscriber ..> TranscriptionResult
    TranscriptionResult *-- TranscribedSegment

    Container --> Settings
    Container --> Database
    Container --> FileStorage
    Container --> LLMClient
    Container --> Embedder
    Container --> VectorStore
    Container --> Transcriber

    %% ---------- services ----------
    class ProjectService
    class AudioService
    class DocumentService
    class StoryService
    class QualityService
    class QuestionService
    class UpdateService
    class JobService
    class AnalysisService
    class AnalysisBuilder {
        +add(analise_lote, fontes) mapa_codigos
        +link_story(codigos, historia_id)
    }
    class ServiceModules {
        <<módulos de funções>>
        indexing / retrieval
        generation / evidence
        versioning / presenters
    }
    ProjectService --> UnitOfWork
    AudioService --> UnitOfWork
    AudioService --> Container
    DocumentService --> Container
    StoryService --> UnitOfWork
    QualityService --> Container
    QuestionService --> Container
    UpdateService --> Container
    JobService --> UnitOfWork
    AnalysisService --> UnitOfWork
    ServiceModules ..> AnalysisBuilder : generation
    AudioService ..> ServiceModules
    DocumentService ..> ServiceModules
    UpdateService ..> ServiceModules
    ServiceModules ..> AppError : lança
```

## 3. Separação entre UI, estado, domínio e persistência

- **UI:** fora deste repositório. O frontend é um projeto separado, em outra origem, e se conecta pelo
  contrato da API (OpenAPI + `docs/ux-guidelines.md`): rótulos legíveis (`status_rotulo`), estados
  explícitos, URLs de mídia com marcação de tempo, listas no padrão `{"itens","total"}`.
- **Estado:** o estado do fluxo vive no banco, nunca em memória do processo: status de fontes,
  jobs, HU, perguntas e sugestões. Isso permite reiniciar a API sem perder nada e consultar
  progresso por polling.
- **Domínio:** `app/domain` concentra o que é regra verificável sem I/O (QUS, template de HU,
  similaridade, chunking). Os serviços combinam domínio + persistência + IA.
- **Persistência:** SQLite é a fonte de verdade; ChromaDB é índice derivado e reconstruível a partir
  de `trechos_fonte`; arquivos em disco são referenciados por caminho.

### 3.1 Modelo de domínio

As entidades não são classes Python: são tabelas de `app/db/schema.sql`, manipuladas como `dict`
pelos repositórios. Linha cheia com losango (`*--`) = chave estrangeira com `ON DELETE CASCADE`;
linha tracejada (`..>`) = referência lógica sem FK (`trecho_id`, `evidencia_ids`, `fonte_id`
polimórfico para áudio ou documento). Cada `TrechoFonte` tem um vetor correspondente no ChromaDB.

```mermaid
classDiagram
    direction LR
    class Projeto {
        id
        titulo
        descricao
    }
    class Audio {
        nome_arquivo
        duracao_segundos
        status
    }
    class SegmentoTranscricao {
        posicao
        inicio_segundos
        fim_segundos
        texto
        texto_original
        editado
    }
    class Documento {
        nome_arquivo
        texto_extraido
        total_paginas
        status
    }
    class TrechoFonte {
        tipo_fonte
        fonte_id
        texto
        localizacao
    }
    class Historia {
        codigo
        perfil
        funcionalidade
        beneficio
        criterios_aceitacao
        status
        informacoes_ausentes
        versao_atual
        qualidade_porcentagem
    }
    class VersaoHistoria {
        numero
        tipo_alteracao
        conteudo
        evidencia_ids
        responsavel
    }
    class Evidencia {
        tipo_fonte
        trecho
        localizacao
        afirmacao
        origem
    }
    class AvaliacaoQualidade {
        versao_historia
        porcentagem
        criterios_atendidos
        avaliacao_criterios
        lacunas
    }
    class PerguntaFollowup {
        defeito_chave
        pergunta
        status
        resposta
    }
    class SugestaoAtualizacao {
        tipo
        conteudo_atual
        conteudo_proposto
        status
        versao_aplicada
    }
    class Job {
        id
        tipo
        status
        parametros
        resultado
    }
    class Analise {
        origem
        sistema
        fontes
        revisada
    }
    class ItemAnalise {
        tipo
        codigo
        texto
        complemento
        referencias
        evidencias
        historia_ids
    }

    Projeto "1" *-- "*" Audio
    Projeto "1" *-- "*" Documento
    Projeto "1" *-- "*" Historia
    Projeto "1" *-- "*" Job
    Projeto "1" *-- "*" TrechoFonte
    Audio "1" *-- "*" SegmentoTranscricao
    Audio "1" ..> "*" TrechoFonte : indexado em
    Documento "1" ..> "*" TrechoFonte : indexado em
    Historia "1" *-- "*" VersaoHistoria
    Historia "1" *-- "*" Evidencia
    Historia "1" *-- "*" AvaliacaoQualidade
    Historia "1" *-- "*" PerguntaFollowup
    Historia "0..1" *-- "*" SugestaoAtualizacao
    Evidencia "*" ..> "0..1" TrechoFonte : cópia de
    VersaoHistoria "*" ..> "*" Evidencia : evidencia_ids
    PerguntaFollowup "*" ..> "0..1" AvaliacaoQualidade
    SugestaoAtualizacao "*" ..> "0..1" Job
    Projeto "1" *-- "*" Analise
    Analise "1" *-- "*" ItemAnalise
    Analise "*" ..> "0..1" Job : gerada por
    ItemAnalise "*" ..> "*" TrechoFonte : evidências (cópia)
    ItemAnalise "*" ..> "*" Historia : historia_ids (funcionalidade)
```

## 4. Fluxos principais

**Ingestão de áudio:** upload → validação → disco + linha `pendente` → `202` → tarefa: `processando`
→ Whisper → segmentos → janelas (`trechos_fonte`) → embeddings → Chroma → `concluido` (ou `erro`).

**Correção de transcrição:** `PATCH segmento` → texto revisado salvo (original preservado) → janelas do
áudio reconstruídas e reindexadas (o mesmo texto revisado alimenta as próximas extrações).

**Análise das fontes:** fontes enviadas → `POST /projetos/{id}/analises/gerar` → job `analise_fontes` → lê a
análise mais recente do projeto (se houver) → chamada única ou lotes (com passada de contexto) → LLM devolve
a análise atualizada → mesclagem com a anterior (códigos e edições preservados) → NOVA análise gravada,
não revisada (itens com evidências reais) → job `concluido`. Repete-se sempre que entram novas fontes.

**Revisão e aprovação da análise:** o analista edita os itens (`/analises/{id}/itens`) e aprova a análise
(`PATCH /analises/{id}` com `revisada: true`). Sem análise aprovada, a geração de HU responde `422`.

**Geração de HU:** job (exige análise aprovada: `analise_id` ou a aprovada mais recente) → valida fontes →
**chamada única** quando as fontes cabem em `GENERATION_BATCH_CHARS` ou **lotes** (lotes com transcrição +
RAG de documentos → LLM por lote → consolidação; sem passada de contexto, D32); o prompt traz fontes →
ANALISE DO PROJETO → instruções → normalização (citação conferida, lacunas de conjunto viram
`informacoes_ausentes`) → deduplicação → descarte do que já existe → HU + evidências + versão 1 → FN da
análise ligadas às HU (`historia_ids`) → job `concluido`.

**Avaliação QUS:** HU + backlog → LLM (8 individuais) + LLM (conjunto) + regras determinísticas → avaliação
persistida (histórico) → cache de qualidade na HU.

**Atualização iterativa:** job com novas fontes → para cada HU: RAG restrito às novas fontes → LLM de
impacto → sugestões `complementa/contradiz`, ou `confirma` (trechos citados anexados direto como evidência,
D27); trechos não citados → extração (com a análise aprovada; sem ela, com o contexto das novas
fontes, D32) → deduplicação → consolidação → `novo_requisito` → humano
aprova/edita/rejeita → aplicação gera nova versão + evidências `atualizacao`.

## 5. Decisões técnicas

| # | Decisão | Motivo |
|---|---|---|
| D1 | `sqlite3` da stdlib + repositórios próprios em vez de ORM | Menos dependências, SQL explícito e fácil de auditar; o volume de entidades é pequeno. Repositórios isolam a troca futura por PostgreSQL. |
| D2 | Código em inglês, dados em português | Elimina camadas de mapeamento; contrato JSON fiel ao vocabulário do domínio. |
| D3 | `BackgroundTasks` do FastAPI + tabela `jobs` | Assíncrono sem broker. Estado persistido permite polling e reprocessamento. |
| D4 | Embeddings calculados pelo backend (Ollama `bge-m3`) e enviados ao Chroma | Mantém a stack sem `sentence-transformers`/PyTorch; `bge-m3` é multilíngue (PT-BR). Configurável. |
| D5 | LLM cita rótulos `F1..Fn`; evidência copiada do banco | Rastreabilidade confiável e sem alucinação de trechos com um modelo pequeno (4B parâmetros). |
| D6 | Avaliação QUS híbrida (LLM + regras determinísticas) | Critérios verificáveis (well_formed, unique, uniform) ficam estáveis; o LLM cobre os semânticos. |
| D7 | Versões com snapshot completo + lista de evidências | Consulta de qualquer versão em O(1); rastreabilidade por versão (US-18) trivial. |
| D8 | Evidências imutáveis | Histórico confiável; correções de fonte não reescrevem o passado. |
| D9 | `faster-whisper` opcional | Pesado (CTranslate2); a API funciona sem ele e o áudio vai para `erro` com instrução clara. Também pode rodar fora da API, num servidor remoto (D36). |
| D10 | `format: "json"` + validação + 1 retentativa | Modelos pequenos às vezes fogem do formato; normalização tolera variações de chave/valor. |
| D11 | Geração em lotes de ~6000 caracteres | Cabe no `num_ctx` 8192 do modelo junto com instruções e resposta. |
| D12 | `X-Usuario` opcional como responsável | Atende US-19 CA03 sem autenticação e prepara a integração futura. |
| D13 | Decodificação de áudio própria (`decode_audio` em `integrations/transcriber.py`) | O `faster-whisper` 1.2.1 decodifica com `av.open(..., metadata_errors="ignore")`, argumento removido no PyAV recente (18+), e toda transcrição falha. Decodificamos com PyAV para float32 mono 16 kHz e passamos o array ao modelo: funciona com qualquer versão do PyAV, sem fixar dependência. |
| D14 | LLM `qwen3.5:4b` com `think: false` fixo no payload de `/api/chat` | O modelo vem com *thinking* ligado por padrão; em teste local com `format: json`, gerou 1022 tokens com ele e 66 sem, para um JSON equivalente. Nenhum fluxo usa o raciocínio, e o volume em lotes arriscaria estourar timeout e `num_ctx`. Sem variável de ambiente nova (opção mais simples); `think: false` é aceito também pelo `llama3.2:3b`, então o rollback é só trocar `REQSCRIBA_LLM_MODEL`. |
| D15 | Frontend em projeto separado: URLs `url_*` relativas e erro 500 tratado dentro do CORS | O cliente prefixa as URLs com a origem da API (`docs/ux-guidelines.md` §8), a opção mais simples, sem nova variável de ambiente. O handler de `Exception` roda no `ServerErrorMiddleware`, fora do `CORSMiddleware`; um middleware interno gera o 500 para que ele tenha cabeçalhos CORS, senão o navegador esconderia o erro do frontend. |
| D16 | Agnóstico de LLM: protocolos + adaptadores próprios com `requests` (`ollama` e `openai_compatible`), sem SDKs nem LiteLLM | Os serviços só conhecem `LLMClient` e `Embedder`. Um único adaptador compatível com OpenAI cobre vários servidores locais e em nuvem, sem dependência nova. LLM e embeddings são escolhidos separadamente, e o Ollama continua como padrão. Provedores com API própria entram como novo adaptador, em `_build_client` (`core/container.py`). |
| D17 | Uma coleção do Chroma por modelo de embeddings + `make reindex` | Vetores de modelos diferentes não são comparáveis: misturá-los causa erro de dimensão ou, pior, busca ruim sem aviso. Coleções separadas impedem a mistura, e `/health` sinaliza trechos sem vetor. Coleções antigas ficam em disco, o que permite voltar ao modelo anterior sem recalcular (apenas fontes novas ficariam de fora). |
| D18 | Passada de contexto prévia com itens citáveis `R#` | Os lotes isolados perdiam perfil/benefício citados em outra parte da reunião. Um resumo livre quebraria a rastreabilidade; por isso cada item de contexto precisa citar trechos `F`, e o código resolve `R#` → trechos reais. Itens sem fonte são descartados. Map por lote (não *refine* sequencial): nenhum texto do LLM é reescrito por outra chamada, e cada lote falha isolado. |
| D19 | Consolidação por operações aplicadas pelo código | O LLM vê só as candidatas e devolve `fundir/preencher/contradicoes` sobre rótulos `C#`, sem texto. Assim nenhum texto novo entra na HU sem fonte: preencher copia o campo literal de outra candidata junto com as evidências dela. Candidatas contraditórias nunca são fundidas (nem transitivamente). Limitação herdada: evidência é por história, não por campo. |
| D20 | Prompt de extração 2.0: "entender antes de escrever" + modo chamada única | As HU vinham com ações de projeto, metas de negócio e participantes da reunião como perfil. O prompt pede, no próprio JSON e nesta ordem, a análise (sistema, participantes × usuários, requisitos de negócio, funcionalidades, fora de escopo) e depois as HU, com os 13 critérios QUS e exemplos curtos. Fontes vêm primeiro e instruções no final (melhor em contexto longo). Pensado para modelos maiores (ex.: Llama 4 Scout): quando as fontes cabem em `GENERATION_BATCH_CHARS`, uma chamada lê tudo, e contexto prévio e consolidação são dispensados (o modelo já viu o conjunto). O caminho em lotes continua para fontes maiores e modelos locais pequenos. Sem variável nova: basta aumentar `GENERATION_BATCH_CHARS`. |
| D21 | Análise das fontes persistida (uma por geração), com CRUD por item | O analista precisa ver e corrigir como a IA entendeu a reunião. Uma análise por geração preserva o histórico sem mesclagem automática. Itens em tabela própria (`itens_analise`, colunas genéricas `texto`/`complemento`/`referencias`) permitem editar um item por vez. Evidências são cópias dos trechos reais (rótulos resolvidos pelo código, como em D5). Códigos RN/FN são renumerados globalmente quando há vários lotes. |
| D22 | Análise revisada como contexto confirmado | A análise mais recente com `revisada = true` entra nos prompts de extração (geração e novas fontes) como informação confirmada por humano: papéis de usuário, requisitos de negócio e itens fora de escopo. Ela não é fonte: toda HU continua precisando de rótulos `F`. É descontada do orçamento de cada lote. |
| D23 | Rótulos estritos e citação conferida | `normalize_label` aceitava "FN1" como "F1" (pega o número), o que daria evidência errada sem aviso. Agora só aceita número que não venha colado a letra ou dígito. Cada HU traz uma `citacao` literal, conferida (ignorando acentos e pontuação) contra o texto dos trechos citados; se não bater, vira `afirmacoes_sem_evidencia`. Lacunas de conjunto (`conflito`, `fluxo_incompleto`) viram `informacoes_ausentes` e, por isso, defeitos e perguntas de follow-up. |
| D24 | Avaliação QUS em lote como job (`avaliacao_qualidade`) | Cada avaliação faz 2 chamadas ao LLM (D28); avaliar várias HU numa requisição estouraria timeouts. O job reaproveita `QualityService.evaluate` com uma transação por história (cada nota é gravada assim que sai), registra falhas por história sem interromper o lote e só fica `erro` se nenhuma for avaliada. O frontend oferece o lote ao fim de uma geração, para as HU recém-criadas. |
| D26 | Análise das fontes em job próprio, aprovada antes da geração (substitui a análise por geração de D20–D22) | A etapa "entender as fontes" saiu do prompt de histórias e virou o prompt `ANALISE_FONTES`, reutilizável: roda na primeira ingestão e a cada nova fonte, recebendo a análise mais recente para atualizá-la, sem gerar HU. Cada execução grava uma nova análise (histórico preservado, nada é sobrescrito) que mantém os códigos RN/FN e as edições do analista. Só uma análise aprovada (`revisada = 1`) libera a geração, que a recebe como "ANALISE DO PROJETO" logo após as fontes e o contexto; assim o humano valida perfis, requisitos de negócio e escopo antes de qualquer HU (Human-in-the-Loop). Custo: uma chamada ao LLM a mais por ciclo e uma etapa manual. A análise nova é criada não revisada porque o modelo pode ter acrescentado itens que ninguém conferiu. |
| D27 | Análise travada durante o job `analise_fontes` e só a mais recente editável (`409`) | O job copia a análise atual no início e grava uma nova no fim; edições feitas no meio (ou na análise substituída) sumiriam da nova sem aviso. Bloquear é mais simples e seguro que reconciliar edições e exclusões concorrentes. `409` com `detalhes.motivo` = `analise_em_andamento` (editar, adicionar, excluir item, aprovar, criar ou excluir análise) ou `analise_desatualizada` (com `analise_atual_id`; análises antigas ainda podem ser excluídas). Como os jobs rodam no processo da API, jobs pendentes/processando encontrados na inicialização são marcados como `erro` (`JobService.fail_orphaned`); sem isso, um job órfão travaria a análise para sempre. A interface mostra o modal somente leitura com o motivo e oferece abrir a análise atual. |
| D32 | Geração de HU e análise de novas fontes sem passada de contexto quando há análise aprovada | A passada de contexto (D18) era refeita do zero na geração, sobre as mesmas fontes que o job de análise já tinha lido: no modo lotes, ~1 chamada ao LLM a mais por lote (≈ 2–3 min num documento de 66 mil caracteres). A análise aprovada já cumpre o papel dela, e melhor: usuários (≈ perfis) e requisitos de negócio (≈ objetivos) revisados por um humano, sem o limite de 1.200 caracteres. O único conteúdo exclusivo do contexto, o glossário, virou o item de análise `termo` (D33). A análise das fontes continua com a passada, e a análise de novas fontes a usa só quando o projeto não tem análise aprovada. |
| D33 | Glossário como item `termo` da análise; migração do `CHECK` por recriação da tabela | Termos e significados passam pela revisão do analista como os demais itens e entram na "ANALISE DO PROJETO". O significado vem das fontes; sem definição, fica `null` (nada é inventado). Bancos existentes têm o `CHECK` antigo em `itens_analise.tipo`, e o SQLite não altera `CHECK` com `ALTER TABLE`: `Database.initialize` recria a tabela com o DDL novo e copia os dados (procedimento recomendado pelo SQLite), de forma idempotente. Sem ferramenta de migração nova (D1). |
| D34 | Benefício sempre em texto, nunca código RN | O modelo copiava o código do requisito de negócio ("Como administrador, quero cadastrar usuários, para RN1."). Nos prompts que escrevem histórias (geração e novas fontes), a "ANALISE DO PROJETO" mostra os RN sem código e cada FN traz o texto dos RN que atende ("— benefício: …"); a versão com códigos fica só para a atualização da análise, que precisa preservá-los. Como rede de segurança, `normalize_story` remove códigos RN/FN do benefício; se só sobra o código, o benefício vira ausente (a HU sinaliza a falta) em vez de "para RN1". |
| D35 | Análise exaustiva e atômica; geração sem teto de 1 HU por FN | Nos jobs medidos, o número de HU era sempre igual ao de FN da análise: a análise listava poucas FN e genéricas ("gerenciar pedidos e mesas"), e a geração tratava "pelo menos uma história por FN" como teto. O prompt da análise agora pede todas as capacidades, uma ação por FN, sem verbos genéricos, incluindo necessidades que aparecem como problemas relatados e integrações exigidas, sempre com fonte. O da geração pede uma ou mais HU por FN (FN composta vira várias HU com o mesmo `funcionalidade_ref`) e HU para necessidades das fontes que a análise não listou (`funcionalidade_ref` null). |
| D25 | Excluir áudio interrompe a transcrição (cancelamento cooperativo) | `Transcriber.transcribe` recebe `should_cancel`, consultado após a decodificação e entre segmentos (o `faster-whisper` gera segmentos sob demanda). O sinal é a própria ausência da linha em `audios`, o que cobre também a exclusão do projeto (cascade) sem estado em memória. O `DELETE` responde na hora; a tarefa para no próximo ponto de checagem (fim do segmento atual ou da VAD inicial) e descarta o resultado, sem marcar `erro`. Antes de gravar os segmentos a existência é conferida de novo (evita violar a FK). Se o arquivo estiver aberto no Windows, `FileStorage.remove` só registra aviso. |
| D26 | Prompt de impacto com as regras QUS da extração e merge campo a campo | As sugestões `complementa/contradiz` saíam fora do QUS: o prompt não tinha nenhum critério de qualidade nem a análise aprovada, e campos omitidos pelo modelo apagavam benefício e critérios atuais. Agora o `impact_prompt` reaproveita o bloco `QUS_INDIVIDUAL` (o mesmo texto do prompt de extração), recebe a análise aprovada (D22), mostra os campos separados e manda que necessidades novas fiquem como `sem_relacao`, para irem à etapa de `novo_requisito` em vez de inflar a HU (Atômica). Na proposta, campo ausente ou vazio mantém o valor atual (`_proposal` em `services/updates.py`). `PROMPT_VERSION` 3.1. Como as fontes de um projeto são do mesmo domínio, o RAG devolve relevância 0,53–0,72 para qualquer HU (medido em dados reais, com `llama4:scout`) e o modelo responde `complementa` copiando a história: um `complementa` sem mudança em perfil, funcionalidade, benefício ou no conjunto de critérios (texto normalizado; título, numeração e ordem não contam) é descartado no código, e os trechos seguem para `novo_requisito`. `contradiz` é sempre mantido. Um limite de relevância no RAG foi descartado: não separaria HU relacionadas das não relacionadas nessa faixa. |
| D27 | Fonte nova que confirma uma HU é anexada automaticamente como evidência | Decisão do produto (2026-11-22). Com D26, a fonte nova que só sustenta a HU deixava de aparecer nela, porque antes isso acontecia aprovando uma "sugestão" que copiava a história. O impacto ganhou a relação `confirma`. Ela, e também um `complementa` sem mudança real, anexa **somente os trechos citados** como evidência atual (origem `atualizacao`, afirmação "anexada automaticamente"), sem sugestão e sem nova versão. Sem trecho citado, nada é anexado. O conteúdo da HU nunca muda sem aprovação; a exceção vale só para a rastreabilidade. Limitação: a versão atual mantém sua lista de evidências; as anexadas entram na próxima versão criada (em `novas_evidencia_ids`). O resultado do job lista `evidencias_anexadas`. Não há migração de schema. |
| D28 | Prompts de avaliação QUS com as mesmas definições e exemplos da geração (`EVAL_INDIVIDUAL_CRITERIA`/`EVAL_SET_CRITERIA` em `llm/prompts.py`) | A avaliação usava as descrições curtas de `domain/qus.py`, diferentes das regras com exemplos que guiam a geração: o avaliador cobrava algo distinto do que o gerador buscava. Os textos ficam em `prompts.py`, adaptados para julgar (lacunas vão para `lacunas`, conflitos e fluxos incompletos para `observacao`); `qus.py` mantém os rótulos curtos usados em defeitos e perguntas. A `reescrita_sugerida` é a etapa final da chamada de conjunto (a última da avaliação, sempre feita porque `complete` não tem regra determinística): ela recebe os critérios individuais e determinísticos que não atendem mais as lacunas, e resolve os problemas da própria HU sem chamada extra ao LLM (lacunas de `complete` ficam só na observação; ver D31). |
| D29 | Reescrita que divide a HU é aplicada por `POST /historias/{historia_id}/aplicar-reescrita` | A avaliação pode sugerir a divisão no formato `H1: Como …; H2: Como …` (D28), mas o `PATCH` só aceita uma história e respondia `422`. A rota nova separa as partes (`story_format.split_story_texts`: pelos marcadores `H1:`/`H2:` ou, como o LLM às vezes os omite, por um novo "Como <perfil>, quero" após `;`, `.` ou quebra de linha; um "Como" que não inicia história não divide) e valida todas antes de gravar: a 1ª substitui a HU atual (nova versão manual) e cada parte seguinte vira uma HU nova (`origem` manual, em revisão, versão 1) com cópia das evidências da original, mantendo a origem de cada uma. Na divisão, uma chamada ao LLM (`CRITERIOS_DIVISAO`, `PROMPT_VERSION` 3.4) recebe a HU original com seus critérios, as partes e até 8 evidências, e escreve critérios próprios para cada parte (reaproveita os da original que se aplicam, sem repetir; novos só com base na história ou nas evidências). Parte sem critério: a original mantém os atuais e a HU nova recebe `informacoes_ausentes` de `criterios_aceitacao`. O LLM é chamado antes de qualquer gravação: falha → `502` e nada muda. A reescrita sem divisão não chama o LLM. O `PATCH` mantém a semântica de editar uma única história. |
| D30 | PDF extraído em modo layout do `pypdf`, com espaços normalizados | Em PDFs exportados pelo Google Docs, o modo padrão de `extract_text` devolve cada palavra numa linha (`"Semana\n \n1\n \n-…"`), o que deixava o texto ilegível na interface e piorava trechos, embeddings e citações. O modo `layout` mantém as linhas do documento; `normalize_whitespace` remove os espaços de alinhamento que ele insere e deixa no máximo uma linha em branco seguida. Se o modo layout falhar numa página, volta ao modo padrão. Documentos já processados só mudam ao serem reprocessados (excluir e reenviar). |
| D31 | Reescrita da avaliação sem cópias de HU do backlog; `sugestao_scriba` é sempre a da última avaliação | A Etapa 2 mandava resolver todos os problemas, inclusive `complete` ("falta a história de criar o pedido"), com o backlog no prompt: o modelo dividia a HU e copiava irmãs como H1/H3 (ex.: US-009 → US-001, US-007 e US-010), e aplicar a sugestão (D29) criaria duplicatas. O ciclo aparece depois de aplicar uma divisão: as HU novas, ao serem avaliadas, voltam a sugerir as irmãs. Prompt (`PROMPT_VERSION` 3.5): reescrever só a HU alvo, dividir só o conteúdo dela, nunca repetir HU do backlog, lacunas de `complete` ficam na observação. Código (`_drop_backlog_copies` em `services/quality.py`), porque o prompt sozinho não garante: separa as partes com `split_story_texts` (as mesmas que `apply_rewrite` criaria) e descarta a parte ≥ 0,90 parecida com outra HU ativa (limite do `unique`) **e** mais parecida com ela do que com a alvo, para não descartar uma reformulação da alvo quando ela tem uma vizinha parecida. Remonta: nenhuma parte → sem sugestão; uma → texto sem `H1:`; várias → `H1: …; H2: …` renumerado. Uma parte idêntica à HU atual (ignorando só espaços e ponto final; correção de pontuação conta) também vira "sem sugestão". `sugestao_scriba` passou a ser sempre a da última avaliação (antes `rewrite or anterior` mantinha na tela uma sugestão antiga, com as cópias, quando a nova vinha vazia). Avaliações gravadas não mudam; reavaliar atualiza. |
| D36 | Transcrição remota pelo contrato `/v1/audio/transcriptions` da OpenAI + servidor próprio mínimo (repositório separado `req-scriba-asr`) | Modelos grandes do Whisper (`large-v3`) precisam de GPU, que fica numa máquina remota sem root, acessada por túnel SSH como o Ollama. O backend continua local; o servidor só carrega o modelo, recebe o áudio, devolve os segmentos e apaga o arquivo temporário. `REQSCRIBA_TRANSCRIPTION_PROVIDER` escolhe `faster_whisper` (padrão, como antes) ou `openai_compatible` (`RemoteTranscriber`, com `requests`, sem dependência nova; o tratamento de erros é o mesmo dos LLMs, via `request_json`). Usamos um script próprio (~100 linhas: FastAPI + faster-whisper) em vez de um servidor pronto (ex.: `speaches`), porque a instalação sem Docker e sem root é mais leve e controlada. Por ser autônomo, ele copia `decode_audio` (D13). O contrato é o da OpenAI, então outros serviços compatíveis também servem. Limitação: o cancelamento (D25) só é conferido antes do envio e ao receber a resposta, e o servidor termina o trabalho mesmo se o áudio for excluído. Ele fica num repositório separado porque é clonado sozinho na máquina remota; o contrato é testado nos dois lados (`req-scriba-asr/tests/test_server.py` e os testes do `RemoteTranscriber` em `tests/test_integrations.py`). |
| D37 | Histórias e critérios de aceitação em etapas separadas (`GERACAO_US_QUS` → `GERACAO_CRITERIOS_ACEITACAO`) | Um único prompt escrevia a HU e no mínimo 3 critérios "que façam sentido", e o modelo inventava regras para cumprir a cota. Agora a etapa de HU não produz critérios (os que vierem são descartados e contados) e uma etapa própria escreve critérios Dado/Quando/Então só com base nas evidências da HU e em poucos trechos relacionados (RAG), aceitando zero critérios com lacuna explícita. As HU vão em lotes por chamada, para não multiplicar chamadas. Nada é gravado antes da etapa de critérios: a v1 já tem os critérios e não há versão extra. A etapa não é fatal; a HU sem critérios fica com a lacuna e pode ser regerada (`/criterios/gerar`). Sob demanda, o LLM não escreve em HU aprovada (`409`), mantendo a regra Human-in-the-Loop. Os critérios continuam no campo JSON `historias.criterios_aceitacao` (sem migração), agora com `dado/quando/entao/fontes/origem`; a decisão de mover para tabela própria fica para o Milestone 2. O impacto de novas fontes (`ANALISE_IMPACTO`) ainda propõe critérios e será substituído no Milestone 3. `CRITERIOS_DIVISAO` (divisão de HU) foi mantido. |
| D38 | Prompts em `app/llm/templates/*.md` (`string.Template`) e schemas em `app/llm/schemas/*.json`, sem dependência nova | Permite versionar e comparar prompts (cabeçalho `versao`, registrado em `resultado.versoes_prompt`) sem mexer na lógica. `$placeholder` evita escapar as chaves dos exemplos JSON (problema dos f-strings). Os schemas são o contrato da resposta (um teste confere que o exemplo do prompt tem todas as chaves obrigatórias) e podem ir ao provedor como saída estruturada (`REQSCRIBA_LLM_STRUCTURED_OUTPUT`, desligada até medirmos o efeito na qualidade do Qwen). Não usamos `jsonschema` para validar: a normalização tolerante de `parsing.py` continua sendo a validação, porque modelos locais variam chaves. Só os prompts novos foram migrados; os demais seguem em `prompts.py` e migram quando forem reescritos. |
| D39 | Evidência por afirmação e validação estrutural em `domain/traceability.py` | Cada fonte citada traz o que sustenta (perfil, funcionalidade, benefício; dado, quando, então) e a própria citação, conferida contra o texto daquele trecho, não contra a união dos trechos. O que o trecho sustenta vai para `evidencias.afirmacao` (coluna existente). O código só faz validação estrutural (referência inexistente, citação divergente, critério sem fonte, campos ausentes) e grava os problemas como `afirmacoes_sem_evidencia`; a validação semântica (o trecho sustenta mesmo?) é do Milestone 2. |
| D40 | Etapas do job registradas em `resultado.etapas` (`services/pipeline.py`) | Para identificar onde um job falhou e quanto cada etapa leva, sem tabela nova: cada etapa grava status e duração, inclusive no job com erro. As etapas são gravadas também durante o job (status `em_andamento`), e a interface mostra a etapa atual. Tokens por chamada ficam para o Milestone 7 (o Ollama já os informa no log). |
| D41 | Critérios de aceitação em tabela própria (`criterios_aceitacao`), com migração idempotente | Substitui a parte "sem migração" de D37. Cada critério tem id permanente (`criterio_id`), a versão da HU em que entrou (`versao_historia`) e status (`vigente`/`substituido`); um critério substituído por edição ou nova geração nunca é apagado (`substituido_na_versao`). Cada versão da HU lista os critérios vigentes nela (`versoes_historia.criterio_ids`), como já fazia com as evidências, e o snapshot continua guardando o texto dos critérios. A reconciliação fica num único ponto (`services/criteria_store.sync_criteria`): mesmo texto normalizado mantém a linha e o id; texto novo vira linha nova; o que saiu da lista é marcado substituído. As fontes do critério recebem o `evidencia_id` da evidência da HU que copia o trecho. Para não mexer nos leitores, o repositório de histórias monta `criterios_aceitacao` a partir da tabela (só vigentes) e recusa gravar a coluna antiga. Migração (`Database.initialize`, registrada em `migracoes`): os critérios do JSON viram linhas vigentes da versão atual (origem `legado`, ou `ia`/`manual` quando o JSON dizia), e a versão atual passa a listar esses ids; versões antigas ficam com `criterio_ids` vazio, porque não há como saber quais critérios valiam nelas. A coluna `historias.criterios_aceitacao` não é alterada nem apagada (rollback). Num banco real (237 HU, 650 critérios) a migração levou 0,01 s e preservou todos. |
| D42 | Validação estrutural da rastreabilidade em `domain/traceability.validate_story` (código puro) | Separa o que o código afirma com certeza do que exige julgamento. Verifica: campos obrigatórios (HU, evidência, critério); ids duplicados (trecho repetido na HU, código de critério repetido, número de versão); trecho que não existe mais (fonte excluída ou reprocessada; aviso, a cópia da evidência fica preservada, D8); texto do trecho diferente da cópia da evidência (ex.: transcrição corrigida; aviso); critério citando trecho que não é evidência da HU; componentes sustentados fora do vocabulário (`perfil/funcionalidade/beneficio`, `dado/quando/entao`); versão do critério fora do intervalo; versões que apontam evidências ou critérios inexistentes; critério sem fonte e componente da HU que nenhuma evidência declara sustentar (avisos). `erro` torna a HU inválida; `aviso` não. Evidências antigas, sem `afirmacoes_sustentadas`, não geram aviso por componente, porque não há o dado para conferir. O que cada trecho sustenta passou a ser coluna estruturada (`evidencias.afirmacoes_sustentadas`), não só texto em `afirmacao`. |
| D43 | Auditoria semântica (`VALIDACAO_EVIDENCIAS`) gravada para revisão, sem aplicar nada | Responde se o trecho sustenta mesmo a afirmação. Cada componente da HU e cada critério vão ao modelo só com os trechos ligados a eles, para que uma evidência compatível com a HU não justifique um critério mais específico. O código decide o que não precisa de julgamento: item sem trecho é `SEM_EVIDENCIA` (e, se nenhum item do lote tem trecho, não há chamada ao LLM); item que o modelo não devolveu, classificação desconhecida ou trecho avaliado fora dos vinculados viram `INSUFICIENTE` ou problema registrado, nunca `COMPATIVEL`; o resumo é contado pelo código. O resultado fica em `auditorias_evidencia` (histórico, versão da HU auditada, modelo e versão do prompt) e aparece em `auditoria_evidencias` no detalhe da HU, marcado `desatualizada` quando a HU muda. `correcao_proposta` é só texto. O analista confirma ou contesta cada item (`revisoes`); a HU nunca muda pela auditoria. Roda como etapa não fatal ao fim da geração (`REQSCRIBA_GENERATION_EVIDENCE_AUDIT`), sob demanda e em lote. Por custo, roda uma vez depois dos critérios, avaliando a HU e cada critério separadamente, em vez de duas passadas (após HU e após critérios). A análise aprovada não entra no prompt: ela não é evidência e não ajuda a julgar se um trecho sustenta uma afirmação. |
| D44 | Evolução incremental (Milestone 3): recuperação por sinais + `ANALISE_IMPACTO_FONTES` em lote | Substitui D26: a análise antiga fazia uma chamada ao LLM por história (o RAG sempre devolvia top-k, então toda HU ia ao modelo) e outra extração para novos requisitos. Agora (`services/updates.py`): só as fontes novas são processadas (fontes já usadas por um job concluído de geração ou de atualização são ignoradas, salvo `reprocessar`); as histórias candidatas de cada trecho novo vêm de três sinais (`domain/impact.py`): vínculo FN → HU da análise (o trecho novo é evidência de uma FN que registra as HU geradas dela), termos do domínio da análise presentes no trecho e na HU, e posição no ranking de embeddings (`bge-m3`, calculados na hora para trechos novos e HU, sem coleção nova; posição em vez de limiar porque a similaridade fica alta para qualquer par do mesmo domínio). Similaridade nunca é tomada como equivalência: um prompt por lote (trechos novos F + até `IMPACT_MAX_STORIES` HU candidatas, com até `IMPACT_EVIDENCES_PER_STORY` evidências atuais E e os critérios) classifica os impactos e propõe novas HU. Regras do código: impacto sem trecho novo válido, HU fora do lote ou categoria inválida é descartado; COMPLEMENTACAO que muda perfil/funcionalidade/benefício vira MODIFICACAO e MODIFICACAO sem mudança vira COMPLEMENTACAO (`categoria_original` registra); os componentes afetados são calculados pelo código; numa MODIFICACAO, todo critério que o modelo não marcou MANTER fica REVISAR; CONTRADICAO nunca muda o conteúdo (a alternativa do modelo fica em `alternativa_proposta`) e recebe os trechos conflitantes (novos e evidências atuais); nova HU ≥ 0,90 parecida com uma HU ativa é descartada e de 0,60 a 0,90 vira `possiveis_duplicidades`; nova HU recebe id temporário (`NOVA-n`) e o código definitivo só ao ser aprovada. Cada proposta guarda `versao_base` (versão da HU na análise) para o Milestone 4. Sem análise aprovada o job roda sem os sinais de FN e termos (a passada de contexto de D18 não é mais usada aqui). Etapas registradas como na geração (D40). |
| D45 | CONFIRMACAO vira proposta pendente (substitui D27) | Pelo plano do Milestone 3 nenhuma alteração é aplicada automaticamente, inclusive a vinculação de evidência: a fonte que só confirma uma HU gera uma proposta `confirmacao` cujo conteúdo proposto é o atual. Aprovar só vincula as evidências novas (origem `atualizacao`), sem nova versão; rejeitar não vincula nada. Uma `complementacao` aprovada também só vincula evidências quando não muda o conteúdo. Custo: mais itens para o analista revisar; ganho: a rastreabilidade também passa por decisão humana. |
| D46 | Versões com fontes motivadoras e status; propostas com CANCELADA e checagem de desatualização (Milestone 4) | Cada versão registra `fontes`: as fontes das evidências vinculadas desde a versão anterior, agrupadas por arquivo (na v1, as da geração; numa edição manual, nenhuma), calculadas em `create_version` sem mudar os chamadores, e `status_historia`. `GET …/versoes/comparacao` compara dois snapshots (campos e diff de texto), os critérios por id permanente (`criterio_ids`) e as evidências, e lista as versões intermediárias. Estados das propostas: `pendente`, `aprovada`, `rejeitada` (discordância de mérito) e `cancelada` (deixou de valer, ex.: desatualizada ou fonte errada; manual com motivo, ou automática ao desaprovar a HU), todos preservados com `decidido_por`, `decidido_em` e `motivo_decisao`. Uma proposta está desatualizada quando a HU mudou depois dela (`versao_base` diferente da atual ou snapshot diferente de `conteudo_atual`): aprovar responde `409 proposta_desatualizada` com a comparação; com `aceitar_desatualizada` aplica sobre a versão atual só os campos que a proposta altera em relação à sua base, preservando edições posteriores e critérios regerados (aplicar o conteúdo inteiro os reverteria). Antes do código definitivo, uma nova necessidade é comparada de novo com o backlog atual (outra candidata pode ter sido aprovada antes): `409 possivel_duplicidade`, salvo `aceitar_duplicidade`. Confirmações não passam pela checagem de desatualização, porque só vinculam evidências. |
| D47 | Critérios ligados à versão e desatualizados quando a HU muda (Milestone 5) | Cada critério guarda a versão em que entrou e a última em que foi validado (`validado_na_versao`) e uma `situacao` (`valido`/`desatualizado`), separada do ciclo de vida (`vigente`/`substituido`). Quando perfil, funcionalidade ou benefício mudam (edição manual, reescrita, proposta aprovada), `apply_content_change` marca como `desatualizado` todo critério que já existia, com motivo e versão: critério antigo não vale automaticamente para a nova versão. Numa proposta de modificação, a indicação da IA (REVISAR/MANTER) entra no motivo, mas não valida o critério. Exceção: na edição manual em que o analista envia a lista de critérios junto, os enviados ficam válidos nessa versão; critérios escritos para a mudança nascem válidos. Mudar só os critérios não desatualiza os demais. O analista confirma (`…/confirmar`, sem nova versão) ou regera. A regeneração é por escopo (desatualizados por padrão, todos, ou critérios escolhidos): os que continuam valendo vão ao prompt como "já validados" (`GERACAO_CRITERIOS_ACEITACAO` 1.1) e são mantidos com o mesmo id; os substituídos ficam preservados. Em HU aprovada, como o LLM não escreve nela, a regeneração vira uma proposta pendente `revisao_criterios` (uma por vez por HU), aprovada pelo fluxo normal, com checagem de desatualização (D46). O lote (`geracao_criterios`) processa HU sem critérios ou com critérios desatualizados (com `substituir`, todas) e cria propostas para as aprovadas. Migração: colunas novas em `criterios_aceitacao` (critérios existentes valem na versão em que entraram) e `revisao_criterios` no `CHECK` de `sugestoes_atualizacao`, por recriação da tabela a partir de qualquer versão anterior do `CHECK`. |
| D48 | Reescrita sugerida em campos, montada pelo código, com prompt em template (`avaliacao_qus_conjunto.md` 1.0; complementa D28) | A reescrita vinha como texto livre e saía do padrão QUS: explicações depois da história ("A especificação do dispositivo… deve ser definida…"), funcionalidades e perfis que a HU não tinha ao dividir, "para que eu possa" misturado com "para". A chamada de conjunto não trazia as regras QUS de escrita, só a lista de problemas. Agora o prompt fica em `templates/avaliacao_qus_conjunto.md`, inclui o bloco compartilhado `_qus_individual.md` (as mesmas regras com exemplos da geração e do impacto) e os campos atuais da HU alvo. O modelo devolve `reescrita_sugerida` como lista de `{perfil, funcionalidade, beneficio}` e deve manter o perfil e o benefício atuais, sem acrescentar o que a HU não traz. `parsing.normalize_rewrite` monta cada parte com `build_story_text` (tira "quero", "para que eu possa" e códigos RN/FN; benefício ausente vira `[A DEFINIR]`, nunca suposição) e numera a divisão como `H1: …; H2: …`, então o contrato da API (string) não muda. Resposta em texto ainda é aceita: só a primeira frase de cada parte no padrão é aproveitada. Uma reescrita que só muda a pontuação volta a ser igual à HU e é descartada. O JSON Schema `avaliacao_qus_conjunto.schema.json` é enviado quando a saída estruturada está ligada. A avaliação individual continua em `prompts.py` (`EVAL_INDIVIDUAL_CRITERIA`, redigida para julgar). |

**Dependências e justificativas:** `fastapi`/`uvicorn` (API), `python-multipart` (upload),
`requests` (provedores de LLM e embeddings, exigido), `chromadb` (vetores, exigido), `pypdf` e `python-docx` (extração
leve, puro Python), `python-dotenv` (carregar `.env`), `faster-whisper` (opcional), `pytest`/`httpx` (dev). O servidor de
transcrição remota fica no repositório `req-scriba-asr`, com seu próprio `requirements.txt`, instalado só na máquina com GPU (D36).

## 6. Trade-offs

| Escolha | Ganho | Custo / mitigação |
|---|---|---|
| SQLite | Zero configuração, arquivo único | Concorrência de escrita limitada → WAL + `busy_timeout`; migração para PostgreSQL via repositórios. |
| BackgroundTasks | Simples, sem infraestrutura | Tarefas rodam no processo da API e se perdem se ele cair → status persistido + reprocessar. |
| Llama 4 Scout local (Ollama) | Privacidade, custo zero, contexto maior que o do Qwen 3.5 4B usado no início | Exige bastante memória e ainda erra em tarefas de julgamento (ex.: `complementa` copiando a HU, D26) → JSON validado, regras determinísticas, humano no loop. Em máquina modesta, `qwen3.5:4b` com `NUM_CTX` 8192 continua funcionando. |
| Sem ORM | Menos abstração | Mais SQL manual → repositórios pequenos e testados. |
| Lotes + deduplicação | Escala com o tamanho das fontes | Pode separar uma necessidade entre lotes → deduplicação por similaridade, contexto prévio e consolidação mesclam. |
| Passadas de contexto e consolidação | Perfil/benefício completos, menos duplicatas reformuladas, contradições sinalizadas | ~1 chamada extra ao LLM por lote + 1 (ou poucas) na consolidação → geração mais lenta com modelo local; cada passada pode ser desligada por variável de ambiente. Só rodam no modo lotes. |
| Chamada única com prompt longo | O modelo vê a reunião inteira e aplica os critérios de conjunto | Exige modelo com contexto grande; com modelo pequeno, `GENERATION_BATCH_CHARS` baixo mantém o modo lotes. Resposta maior (análise + HU) → atenção ao limite de saída do provedor. |
| Busca textual normalizada em Python | Ignora acentos/maiúsculas (PT-BR) | Varre os segmentos de um áudio (volume pequeno); sem ranking → busca semântica separada em `/busca`. |

## 7. API

Base: `http://localhost:8000/api/v1` · OpenAPI interativa em `/docs` (Swagger) e `/redoc`.
Cabeçalho opcional `X-Usuario` (responsável pelas alterações; padrão `engenheiro`).

### Saúde
| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | Status de SQLite, ChromaDB (e reindexação pendente), LLM, embeddings e ASR |

### Projetos
| Método | Rota | Descrição |
|---|---|---|
| POST | `/projetos` | Criar projeto |
| GET | `/projetos` | Listar projetos (com totais e qualidade geral) |
| GET | `/projetos/{projeto_id}` | Detalhar projeto + backlog |
| PATCH | `/projetos/{projeto_id}` | Atualizar título/descrição |
| DELETE | `/projetos/{projeto_id}` | Excluir projeto e todos os dados |

### Áudios e transcrição (US-01..03, US-13)
| Método | Rota | Descrição |
|---|---|---|
| POST | `/projetos/{projeto_id}/audios` | Upload (`multipart`, campo `arquivo`) → `202` |
| GET | `/projetos/{projeto_id}/audios` | Listar áudios do projeto |
| GET | `/audios/{audio_id}` | Detalhes e estado |
| GET | `/audios/{audio_id}/arquivo` | Arquivo de áudio (suporta `Range`, use `#t=` para posicionar) |
| POST | `/audios/{audio_id}/reprocessar` | Nova tentativa de transcrição |
| GET | `/audios/{audio_id}/transcricao` | Transcrição completa + segmentos com tempos |
| GET | `/audios/{audio_id}/transcricao/busca?termo=` | Busca textual nos segmentos |
| PATCH | `/audios/{audio_id}/segmentos/{segmento_id}` | Corrigir texto de um segmento |
| DELETE | `/audios/{audio_id}` | Excluir áudio (interrompe a transcrição em andamento) |

### Documentos (US-04, US-13)
| Método | Rota | Descrição |
|---|---|---|
| POST | `/projetos/{projeto_id}/documentos` | Upload PDF/DOCX/TXT/MD → `202` |
| GET | `/projetos/{projeto_id}/documentos` | Listar documentos |
| GET | `/documentos/{documento_id}` | Detalhes e estado |
| GET | `/documentos/{documento_id}/conteudo` | Texto extraído + trechos indexados |
| POST | `/documentos/{documento_id}/reprocessar` | Nova tentativa de extração/indexação |
| DELETE | `/documentos/{documento_id}` | Excluir documento |

### Busca semântica
| Método | Rota | Descrição |
|---|---|---|
| GET | `/projetos/{projeto_id}/busca?termo=&limite=` | RAG sobre trechos de áudios e documentos |

### Análises das fontes
| Método | Rota | Descrição |
|---|---|---|
| POST | `/projetos/{projeto_id}/analises/gerar` | Analisar `audio_ids`/`documento_ids` (atualiza a análise mais recente) → `202` job `analise_fontes` |
| GET | `/projetos/{projeto_id}/analises` | Listar análises (mais recente primeiro) |
| POST | `/projetos/{projeto_id}/analises` | Criar análise manualmente (com itens opcionais) |
| GET | `/analises/{analise_id}` | Detalhe: sistema, fontes, totais e itens com evidências |
| PATCH | `/analises/{analise_id}` | Editar `sistema` ou aprovar (`revisada: true`, libera a geração de HU) |
| DELETE | `/analises/{analise_id}` | Excluir análise e itens (HU não são afetadas) |
| POST | `/analises/{analise_id}/itens` | Adicionar item (RN/FN recebem código automático) |
| PATCH | `/analises/{analise_id}/itens/{item_id}` | Editar texto, complemento, referências ou trechos |
| DELETE | `/analises/{analise_id}/itens/{item_id}` | Excluir item (RN excluído sai das referências das FN) |

### Histórias de Usuário (US-05, 06, 16–20)
| Método | Rota | Descrição |
|---|---|---|
| POST | `/projetos/{projeto_id}/historias/gerar` | Gerar HU a partir de `audio_ids`/`documento_ids` e de uma análise aprovada (`analise_id` opcional) → `202` job; sem análise aprovada → `422` |
| POST | `/projetos/{projeto_id}/historias` | Criar HU manualmente |
| GET | `/projetos/{projeto_id}/historias?status=` | Listar HU; cada item traz `reescrita_sugerida` da avaliação mais recente (o frontend marca as HU com recomendação aguardando avaliação) |
| GET | `/historias/{historia_id}` | Detalhe completo (QUS, fontes, versões) |
| PATCH | `/historias/{historia_id}` | Editar campos (gera nova versão) |
| POST | `/historias/{historia_id}/aplicar-reescrita` | Aplicar a reescrita da avaliação; `H1: …; H2: …` divide a HU (a 1ª parte atualiza esta, as demais viram HU novas; cada parte recebe critérios próprios gerados pelo LLM) |
| POST | `/historias/{historia_id}/criterios/gerar` | Gerar ou regerar critérios (`escopo` auto/desatualizados/todos ou `criterio_ids`); HU aprovada → proposta `revisao_criterios`; falha do LLM → `502` |
| POST | `/historias/{historia_id}/criterios/{criterio_id}/confirmar` | Confirmar que um critério desatualizado continua válido na versão atual |
| POST | `/projetos/{projeto_id}/criterios/gerar` | Critérios em lote (`historia_ids`, `substituir`) → `202` job `geracao_criterios` |
| GET | `/historias/{historia_id}/validacao-estrutural` | Validação estrutural da rastreabilidade (código, sem LLM) |
| GET | `/projetos/{projeto_id}/validacao-estrutural` | Validação estrutural de todas as HU ativas (totais por tipo) |
| GET | `/trechos/{trecho_id}` | Fonte original de um trecho (arquivo, localização, texto atual) e onde ele é usado |
| POST | `/historias/{historia_id}/auditoria-evidencias` | Auditoria semântica das evidências (`VALIDACAO_EVIDENCIAS`); falha do LLM → `502` |
| GET | `/historias/{historia_id}/auditorias-evidencias` | Histórico das auditorias da HU |
| GET | `/auditorias-evidencias/{auditoria_id}` | Detalhe de uma auditoria |
| POST | `/auditorias-evidencias/{auditoria_id}/revisoes` | Revisão humana de um item (`confirma` ou `contesta`) |
| POST | `/projetos/{projeto_id}/auditoria-evidencias` | Auditoria em lote → `202` job `auditoria_evidencias` |
| POST | `/historias/{historia_id}/aprovar` | Aprovar |
| POST | `/historias/{historia_id}/desaprovar` | Desaprovar (sai do backlog) |
| GET | `/historias/{historia_id}/evidencias?versao=` | Evidências (atuais ou de uma versão) |
| GET | `/historias/{historia_id}/rastreabilidade` | Evidências por versão, com navegação |
| GET | `/historias/{historia_id}/versoes` | Histórico cronológico |
| GET | `/historias/{historia_id}/versoes/comparacao?de=&para=` | Comparar duas versões (padrão: penúltima × última) |
| GET | `/historias/{historia_id}/versoes/{numero}` | Conteúdo de uma versão |

### Qualidade (US-07..09)
| Método | Rota | Descrição |
|---|---|---|
| POST | `/historias/{historia_id}/avaliar` | Avaliar/reavaliar versão atual (QUS) |
| POST | `/projetos/{projeto_id}/historias/avaliar` | Avaliar em lote `historia_ids` (vazio = todas as ativas) → `202` job |
| GET | `/historias/{historia_id}/avaliacoes` | Histórico de avaliações |
| GET | `/historias/{historia_id}/avaliacoes/comparacao?de=&para=` | Comparar duas avaliações |
| GET | `/historias/{historia_id}/defeitos` | Defeitos e lacunas atuais |
| GET | `/projetos/{projeto_id}/qualidade` | Visão geral de qualidade do projeto |

### Perguntas de follow-up (US-10..12)
| Método | Rota | Descrição |
|---|---|---|
| POST | `/historias/{historia_id}/perguntas/gerar` | Gerar perguntas (opcional: `defeitos`) |
| GET | `/projetos/{projeto_id}/perguntas?status=&historia_id=` | Listar perguntas |
| GET | `/projetos/{projeto_id}/followup?agrupar_por=historia\|tipo_defeito` | Lista de follow-up agrupada |
| PATCH | `/perguntas/{pergunta_id}` | Editar texto |
| POST | `/perguntas/{pergunta_id}/aprovar` | Aprovar |
| POST | `/perguntas/{pergunta_id}/rejeitar` | Rejeitar |
| POST | `/perguntas/{pergunta_id}/responder` | Registrar resposta |

### Atualizações iterativas (US-13..15)
| Método | Rota | Descrição |
|---|---|---|
| POST | `/projetos/{projeto_id}/atualizacoes/analisar` | Analisar novas fontes (`audio_ids`, `documento_ids`, `reprocessar`) → `202` job; cria propostas `confirmacao`, `complementacao`, `modificacao`, `contradicao`, `nova_necessidade` |
| GET | `/projetos/{projeto_id}/sugestoes-atualizacao?status=&historia_id=` | Listar sugestões (`pendente`, `aprovada`, `rejeitada`, `cancelada`) |
| GET | `/sugestoes-atualizacao/{sugestao_id}` | Detalhe com comparação atual × proposto |
| PATCH | `/sugestoes-atualizacao/{sugestao_id}` | Editar conteúdo proposto |
| POST | `/sugestoes-atualizacao/{sugestao_id}/aprovar` | Aprovar e aplicar (aceita edição final) |
| POST | `/sugestoes-atualizacao/{sugestao_id}/rejeitar` | Rejeitar com `motivo` opcional (HU preservada) |
| POST | `/sugestoes-atualizacao/{sugestao_id}/cancelar` | Cancelar com `motivo` (proposta preservada) |

### Jobs
| Método | Rota | Descrição |
|---|---|---|
| GET | `/jobs/{job_id}` | Estado e resultado de um job |
| GET | `/projetos/{projeto_id}/jobs` | Jobs do projeto |
