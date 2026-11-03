# Regras Obrigatórias do Projeto

Estas regras são **obrigatórias**. Um PR/entrega que viole qualquer uma delas não está pronto.

## R1 — Contrato da API
1. Todas as saídas da API são JSON — inclusive erros, 404 de rota e 500.
2. Erros seguem `{"erro": {"codigo": str, "mensagem": str, "detalhes": any}}`.
3. Listas seguem `{"itens": [...], "total": n}`.
4. Todo endpoint declara `response_model`, `summary`, `description` e respostas de erro no OpenAPI.
5. Prefixo único `/api/v1`. Mudança incompatível exige nova versão de rota.
6. Operações demoradas retornam `202` e um recurso consultável (fonte com `status` ou `job`).

## R2 — Componentes pequenos e reutilizáveis
1. Um módulo = uma responsabilidade. Funções idealmente < 40 linhas; arquivos < 400 linhas.
2. Integrações externas implementam um protocolo e são injetadas pelo `Container`.
3. Lógica compartilhada vai para `app/domain` (pura) ou `app/core` (infra transversal).

## R3 — Nomes consistentes
1. Código (classes, funções, variáveis, arquivos) em inglês; dados (colunas, chaves JSON, rotas,
   status) em português snake_case sem acento.
2. Arquivos `snake_case.py`; classes `PascalCase`; constantes `UPPER_SNAKE`.
3. Repositórios: `<Agregado>Repository`; serviços: `<Agregado>Service`; schemas: `<Recurso><Ação>`
   (`StoryCreate`, `StoryOut`).

## R4 — Não duplicar lógica
1. Formato de HU, cálculo QUS, similaridade e chunking existem em **um** lugar (`app/domain`).
2. Presenters (`services/presenters.py`) são o único lugar que monta o JSON de resposta das entidades.
3. Versionamento de HU passa sempre por `services/versioning.py`.

## R5 — Não misturar regra de negócio
1. Routers: sem `if` de regra de negócio, sem SQL, sem chamada a LLM.
2. Repositórios: sem decisões de negócio; só leitura/escrita.
3. `domain/`: sem I/O (sem banco, rede, disco, relógio injetado por parâmetro quando necessário).
4. Prompts ficam em `app/llm/prompts.py`; parsing/normalização em `app/llm/parsing.py`.

## R6 — Human-in-the-Loop e honestidade da IA
1. A IA nunca altera uma HU diretamente: gera candidatas, avaliações e sugestões.
2. O texto de uma evidência vem do banco, nunca do LLM.
3. Informação ausente é marcada (`[A DEFINIR: …]` + `informacoes_ausentes`), nunca inventada.
4. Fontes com erro ou não processadas nunca entram em geração/análise.

## R7 — Testes para lógica crítica
Obrigatório ter testes para: pontuação/faixas QUS, normalização de saídas do LLM, formato de HU,
chunking, versionamento, evidências por versão, aplicação/rejeição de sugestões, transições de
status, validação de upload e contrato de erro.

## R8 — Não quebrar funcionalidades existentes
1. Rode a suíte completa (`make test`) antes de concluir qualquer tarefa.
2. Mudança de schema SQL deve ser compatível (`ALTER TABLE ADD COLUMN`) ou acompanhada de migração.
3. Mudança de contrato JSON exige atualizar `docs/specs.md`, `docs/architecture.md` e testes de API.

## R9 — Erros nunca ignorados
1. Proibido `except: pass` ou `except Exception` sem log e sem conversão.
2. Falhas em background são persistidas (`status=erro`, `mensagem_erro`) e reprocessáveis.

## R10 — Dependências
1. Só bibliotecas listadas em `requirements*.txt`. Nova dependência exige justificativa em
   `docs/architecture.md`. Prefira a biblioteca padrão.
2. Transcrição (`faster-whisper`) é opcional e isolada em `requirements-asr.txt`.
