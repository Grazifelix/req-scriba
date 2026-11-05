# PRD — ReqScriba (Backend v1)

## 1. Visão geral do produto

O **ReqScriba** é um ambiente de Engenharia de Requisitos apoiado por LLM com arquitetura
**Human-in-the-Loop**. Ele transforma gravações de reuniões de elicitação e documentos do cliente
em um backlog de Histórias de Usuário (HU) rastreáveis, avaliadas pelo framework **Quality User
Story (QUS)**, e conduz rodadas iterativas de esclarecimento com os stakeholders.

Objetivo geral (pesquisa): desenvolver e avaliar empiricamente uma ferramenta integrada que
automatize elicitação, análise de qualidade, atualização iterativa pós-entrevista e
rastreabilidade de requisitos a partir de transcrições e documentos.

## 2. Problema

- Transcrever e analisar reuniões manualmente é lento e sujeito a perda de informação.
- HU escritas às pressas têm defeitos (ambiguidade, falta de benefício, não atômicas) detectados tarde.
- Lacunas de informação não viram perguntas estruturadas para a próxima reunião.
- Após novas entrevistas, atualizar o backlog é manual e a origem de cada requisito se perde.
- LLMs alucinam: sem rastreabilidade e controle humano, o backlog perde confiabilidade.

## 3. Público-alvo

Engenheiros de requisitos, analistas de negócio, Product Owners e pesquisadores de Engenharia de
Requisitos que conduzem entrevistas com stakeholders.

## 4. Objetivos principais

1. Reduzir o esforço de transcrição e de redação inicial do backlog.
2. Garantir que toda HU gerada seja rastreável até trechos concretos das fontes.
3. Medir a qualidade de cada HU (13 critérios QUS) e do backlog como um todo.
4. Transformar defeitos em perguntas de follow-up revisadas por humanos.
5. Evoluir o backlog com novas fontes mantendo histórico de versões e evidências.
6. Manter o humano no controle: nenhuma saída da IA altera o backlog aprovado sem aprovação.

## 5. Fora de escopo (v1)

- Frontend/UI neste repositório: o frontend é um projeto separado, que consome a API REST documentada.
- Autenticação, autorização e multi-tenancy (preparado para o futuro, não implementado).
- Diarização de falantes, tradução e transcrição em tempo real.
- OCR de documentos digitalizados (PDF imagem é rejeitado com mensagem clara).
- Integração com Jira/Azure DevOps, exportação para ferramentas externas.
- Bancos externos (PostgreSQL, Chroma server) e filas distribuídas.

## 6. Personas

**Ana — Engenheira de Requisitos (persona primária).** Conduz 3–5 entrevistas por semana. Quer
chegar à reunião de validação com perguntas objetivas e um backlog inicial confiável.
Dor: horas transcrevendo e reescrevendo HU.

**Bruno — Product Owner.** Consome o backlog aprovado. Quer saber de onde veio cada requisito e
o que mudou após cada entrevista. Dor: requisitos "sem dono" e sem justificativa.

**Carla — Pesquisadora.** Avalia empiricamente a ferramenta. Precisa de dados estruturados
(avaliações históricas, versões, origem manual vs. IA) para medir efetividade.

## 7. Jornada do usuário

1. Ana cria um **projeto**.
2. Envia gravações (US-01) → acompanha estado (`pendente → processando → concluido/erro`).
3. Revisa a **transcrição** com marcações de tempo, busca termos e corrige erros (US-02, US-03).
4. Importa **documentos** do cliente (US-04); o texto é dividido em trechos e indexado (RAG).
5. Seleciona fontes e solicita a **geração de HU** (US-05) → HU candidatas com evidências (US-16).
6. Revisa/edita/aprova/desaprova HU (US-06), consultando evidências (US-17).
7. Solicita **avaliação QUS** (US-07), corrige e **reavalia** (US-08); vê **lacunas** (US-09).
8. Gera **perguntas de follow-up** a partir dos defeitos (US-10), revisa (US-11) e organiza (US-12).
9. Após a nova entrevista, registra respostas, adiciona as **novas fontes** (US-13) e pede análise.
10. Recebe **sugestões de atualização** com comparação atual × proposto (US-14) e aprova/edita/rejeita (US-15).
11. Bruno consulta **histórico de versões** (US-19, US-20) e evidências por versão (US-18).

## 8. Funcionalidades essenciais (v1)

| Módulo | Funcionalidades | US |
|---|---|---|
| Fontes de áudio | upload, validação, estados, transcrição com tempos, reprocessamento, busca, correção, áudio com seek | 01–03 |
| Documentos | upload, validação, extração de texto, chunking, indexação vetorial, falha explícita | 04 |
| Histórias | geração por LLM, estrutura padronizada, lacunas explícitas, revisão humana, CRUD, aprovação | 05–06 |
| Qualidade | avaliação QUS (8 individuais + 5 de conjunto), percentual e faixa, histórico, comparação, visão geral | 07–09 |
| Follow-up | perguntas por defeito, limitações explícitas, revisão, agrupamento, respostas | 10–12 |
| Evolução | análise de novas fontes via RAG, sugestões (complementa/contradiz/novo requisito), diff, aplicação controlada | 13–15 |
| Rastreabilidade | evidências por HU com localização, múltiplas fontes, ausência de suporte sinalizada, evidências por versão | 16–18 |
| Versionamento | versão inicial, nova versão a cada alteração, origem manual/IA, responsável, data | 19–20 |

## 9. Funcionalidades futuras

Autenticação (OAuth2/JWT) e papéis; diarização de falantes; OCR;
exportação (CSV, Jira); fila de tarefas dedicada (RQ/Celery) para múltiplos workers; PostgreSQL +
Chroma server; painel de métricas da pesquisa; avaliação comparativa entre modelos LLM.

## 10. Regras de negócio

- **RN01** Uma HU gerada pela IA nasce com status `em_revisao`, ou `falta_informacao` quando há
  elemento ausente (perfil, funcionalidade ou benefício).
- **RN02** Apenas HU `aprovada` compõem o backlog aprovado; `desaprovada` nunca integra o backlog.
- **RN03** HU não aprovadas exibem `validada: false` e rótulo de status distinto.
- **RN04** Formato padrão: "Como [perfil], quero [funcionalidade], para [benefício]". Elemento ausente
  é exibido como `[A DEFINIR: <elemento>]` e listado em `informacoes_ausentes`.
- **RN05** Qualidade = critérios QUS atendidos / 13 × 100 (2 casas). Só o resultado "Atende" conta.
  Faixas: 0–24 Péssima · 25–49 Baixa · 50–74 Regular · 75–99 Alta · 100 Excelente.
- **RN06** Qualidade geral do projeto = média das porcentagens das HU avaliadas não desaprovadas.
- **RN07** Toda avaliação é preservada; reavaliar cria nova avaliação vinculada à versão atual.
- **RN08** Criar HU cria a versão 1; cada alteração de conteúdo salva cria versão N+1, com tipo
  (`manual`/`automatica`), origem, responsável e data.
- **RN09** Sugestões de atualização nunca alteram a HU diretamente; só a aprovação aplica.
- **RN10** Evidências são imutáveis; cada versão guarda a lista de evidências que a fundamentavam.
- **RN11** Fontes com status diferente de `concluido` não podem ser usadas em geração/análise.
- **RN12** Perguntas rejeitadas não integram a lista de follow-up; aprovadas ficam disponíveis para a
  próxima rodada; respondidas guardam a resposta vinculada à pergunta e à HU.
- **RN13** O LLM não pode inventar detalhes: afirmações sem evidência são sinalizadas.

## 11. Requisitos funcionais (user stories)

> Fonte de verdade funcional. Cada CA deve ter cobertura em `docs/test-plan.md`.

### US-01 — Importação de gravações
Como engenheiro de requisitos, quero enviar gravações de reuniões para que sejam processadas pelo mecanismo de transcrição.
- CA01: arquivo em formato suportado é armazenado e associado ao projeto.
- CA02: arquivo inválido/não suportado é rejeitado informando o motivo.
- CA03: o estado do processamento é exposto (pendente, processando, concluído, erro).

### US-02 — Transcrição automática de áudio
Como engenheiro de requisitos, quero converter automaticamente o conteúdo das gravações em texto para evitar a transcrição manual.
- CA01: ao concluir, a transcrição textual fica disponível.
- CA02: segmentos são organizados em blocos com referências temporais.
- CA03: falhas são sinalizadas e é possível tentar novamente.

### US-03 — Consulta e revisão da transcrição
Como engenheiro de requisitos, quero visualizar e corrigir a transcrição para garantir que a extração represente a reunião.
- CA01: trechos textuais organizados. CA02: busca por palavra/expressão retorna trechos.
- CA03: correção salva passa a ser usada nas próximas extrações.
- CA04: trecho com referência temporal permite localizar o momento na gravação.

### US-04 — Importação de documentos
Como engenheiro de requisitos, quero importar documentos do cliente para extrair informações textuais.
- CA01: documento suportado é associado ao projeto. CA02: texto extraível fica disponível para análise.
- CA03: falha é informada e nenhum conteúdo inexistente é usado na geração.

### US-05 — Geração automática de Histórias de Usuário
Como engenheiro de requisitos, quero gerar HU a partir das fontes selecionadas para acelerar o backlog inicial.
- CA01: fontes válidas geram HU candidatas. CA02: estrutura padronizada.
- CA03: informação insuficiente é indicada como ausente, sem suposições como fatos.
- CA04: HU candidatas ficam disponíveis para revisão humana.

### US-06 — Revisão e aprovação das histórias geradas
Como engenheiro de requisitos, quero revisar, editar, aprovar ou rejeitar HU sugeridas pela IA.
- CA01: visualizar e editar campos. CA02: aprovar registra status aprovada.
- CA03: rejeitada não integra o backlog aprovado. CA04: HU não validada é identificável.

### US-07 — Visualização dos resultados de qualidade
- CA01: critérios analisados e resultados visíveis. CA02: violação traz justificativa.
- CA03: visão geral identifica HU com problemas.

### US-08 — Reavaliação após correções
- CA01: nova análise referente à versão atual. CA02: mudança de defeito indicada sem apagar o registro anterior.

### US-09 — Identificação de lacunas de informação
- CA01: possível lacuna sinalizada. CA02: informação ausente/insuficiente descrita. CA03: sem inventar detalhes.

### US-10 — Geração de perguntas a partir dos defeitos
- CA01: ao menos uma pergunta por defeito quando houver contexto. CA02: pergunta identifica HU e defeito.
- CA03: perguntas contextualizadas por tipo de defeito. CA04: limitação indicada quando não há contexto.

### US-11 — Revisão das perguntas sugeridas
- CA01: editar texto. CA02: rejeitada fora da lista aprovada. CA03: aprovada disponível para a próxima rodada.

### US-12 — Organização das perguntas para novas entrevistas
- CA01: perguntas aprovadas vinculadas aos requisitos. CA02: agrupar por HU ou tipo de defeito.
- CA03: resposta registrada associada à pergunta e ao requisito.

### US-13 — Incorporação de novas fontes de informação
- CA01: nova fonte associada ao projeto. CA02: análise considera requisitos existentes.
- CA03: complemento/contradição sinalizados como possível atualização.

### US-14 — Sugestão automática de atualização
- CA01: sugestões de alteração pertinentes. CA02: comparação atual × proposto.
- CA03: informação sem correspondência pode virar novo requisito. CA04: HU aprovada nunca é modificada diretamente.

### US-15 — Validação e aplicação das atualizações
- CA01: aprovar, rejeitar ou editar. CA02: aprovada modifica a HU. CA03: rejeitada preserva a HU.
- CA04: atualização registrada no histórico de versões.

### US-16 — Associação automática de fontes aos requisitos
- CA01: trechos registrados como evidência. CA02: múltiplas evidências por HU.
- CA03: ausência de suporte documental sinalizada. CA04: referência à fonte e à localização preservadas.

### US-17 — Visualização das evidências
- CA01: lista de evidências. CA02: trecho de transcrição com tempo. CA03: trecho de documento com localização.
- CA04: navegação entre múltiplas evidências.

### US-18 — Manutenção da rastreabilidade após atualizações
- CA01: novas evidências registradas na atualização. CA02: associações históricas preservadas.
- CA03: evidências identificáveis por versão.

### US-19 — Registro de alterações
- CA01: versão inicial na criação. CA02: nova versão a cada alteração. CA03: data, responsável e versão.
- CA04: origem IA identificável.

### US-20 — Visualização do histórico de versões
- CA01: versões em ordem cronológica. CA02: conteúdo de versão anterior. CA03: só versão inicial mostra registro de criação.

## 12. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF01 | Todas as respostas da API em JSON (inclusive erros), documentadas em OpenAPI (`/docs`). |
| RNF02 | Execução local por padrão: SQLite, ChromaDB e Ollama na máquina do usuário; dados não saem dela, salvo se um provedor de LLM ou embeddings externo for configurado explicitamente. |
| RNF03 | Operações longas (transcrição, extração, geração, análise) são assíncronas com estado consultável. |
| RNF04 | Uploads: áudio até 200 MB, documento até 20 MB (configuráveis). |
| RNF05 | Respostas de leitura em < 300 ms para projetos com até 500 HU (sem chamada ao LLM). |
| RNF06 | Robustez a saídas malformadas do LLM: validação, normalização e erro explícito. |
| RNF07 | Testabilidade: integrações externas substituíveis por fakes; suíte roda sem Ollama/Whisper. |
| RNF08 | Manutenibilidade: camadas separadas, sem duplicação, decisões documentadas. |
| RNF09 | Escalabilidade: troca de SQLite → PostgreSQL e Chroma local → servidor sem mudar serviços. |
| RNF10 | Segurança básica: validação de entradas, nomes de arquivo saneados, sem dados sensíveis em logs. |

## 13. Métricas de sucesso

- Tempo médio da gravação ao backlog inicial revisado (meta: −50% vs. processo manual).
- % de HU geradas aprovadas sem edição e com edição leve.
- Qualidade QUS média do backlog na 1ª geração vs. após reavaliação (meta: +20 p.p.).
- % de HU com ao menos uma evidência (meta: ≥ 95%).
- % de perguntas geradas aprovadas pelos engenheiros (meta: ≥ 60%).
- % de sugestões de atualização aceitas (aprovadas ou editadas).

## 14. Critérios de aceite do produto (v1)

- Todos os CAs de US-01 a US-20 implementados e cobertos por testes ou roteiro manual.
- Documentação da API completa em `/docs`; todos os endpoints funcionais.
- Dados persistem em SQLite (`data/reqscriba.db`) e ChromaDB (`data/chroma/`) entre reinícios.
- `make test` sem falhas; README permite instalar e rodar do zero.
