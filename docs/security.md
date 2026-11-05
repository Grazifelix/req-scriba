# Segurança — ReqScriba Backend v1

O ReqScriba roda localmente e processa material potencialmente confidencial (reuniões e documentos
de clientes). As práticas abaixo são obrigatórias.

## 1. Sanitização e validação de entradas

- Todo corpo JSON é validado por Pydantic com `extra="forbid"`, tipos estritos, limites de tamanho
  (ex.: título ≤ 200, textos de HU ≤ 2000, pergunta ≤ 1000) e enums para status.
- Strings são aparadas; strings compostas só de espaços são rejeitadas.
- Parâmetros de busca têm tamanho mínimo/máximo; a busca textual é feita em Python sobre texto
  normalizado (sem acentos, sem diferenciar maiúsculas), sem interpolar o termo em SQL.
- **SQL injection:** 100% das consultas usam parâmetros (`?`); nenhuma concatenação de valores do usuário.

## 2. Uploads

- Lista branca de extensões; verificação de assinatura binária (magic bytes); limite de tamanho
  aplicado durante a escrita em streaming (o arquivo nunca é lido inteiro na memória).
- Nome do arquivo saneado (`[A-Za-z0-9._-]`, sem `..`, sem caminhos) e prefixado por UUID;
  o nome original só é guardado como metadado.
- Arquivos ficam fora de qualquer diretório servido estaticamente; o download passa pela API,
  que resolve o caminho a partir do banco e confere que está dentro de `data/uploads/`.

## 3. Evitar XSS

- A API só responde `application/json` (exceto o download do áudio, com `content-type` de áudio e
  `Content-Disposition: inline` com nome saneado).
- Conteúdo vindo do LLM, de transcrições e de documentos é tratado como **texto não confiável**:
  é armazenado como está e o frontend deve renderizá-lo escapado (nunca como HTML).
- Cabeçalhos de segurança: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`.

## 4. Injeção de prompt

- Fontes são delimitadas e rotuladas; o prompt instrui o modelo a tratá-las como dados.
- A saída do LLM nunca executa ações: é validada, normalizada e vira sugestão sujeita a aprovação.
- Evidências são copiadas do banco, não do texto gerado.

## 5. Dados sensíveis

- Nenhum segredo no código; configuração via ambiente/`.env` (`.env` no `.gitignore`).
- Logs não contêm conteúdo de transcrições, documentos ou HU — apenas ids, tamanhos e durações.
- Mensagens de erro 500 são genéricas; o detalhe técnico fica só no log.
- Todo processamento é local (Ollama, Whisper, SQLite, Chroma): nenhum dado sai da máquina.
- Excluir um projeto remove banco, vetores e arquivos associados.

## 6. Validação no cliente

O frontend (projeto separado) deve repetir validações básicas (extensão, tamanho, campos obrigatórios) para
feedback imediato — **mas a validação do servidor é a única confiável** e nunca deve ser relaxada.

## 7. Preparação para autenticação futura

- Cabeçalho `X-Usuario` já identifica o responsável em versões, decisões e respostas; será
  substituído pela identidade do token (JWT/OAuth2) sem mudar os serviços.
- Dependência `get_actor()` em `api/deps.py` é o único ponto a alterar.
- Todas as entidades pertencem a um `projeto_id`, permitindo escopo por usuário/equipe.

## 8. Separação clara de dados

- Cada recurso é acessado por id e validado contra seu projeto (fontes de outro projeto são rejeitadas
  na geração/análise).
- Vetores no Chroma carregam `projeto_id` e toda consulta filtra por ele.
- Uploads separados por pasta `projeto_<id>`.

## 9. Disponibilidade e abuso

- Limites de tamanho de upload e de lote do LLM evitam exaustão de memória.
- CORS restrito às origens configuradas em `REQSCRIBA_CORS_ORIGINS`: liste as origens exatas do frontend,
  sem curinga. CORS só limita navegadores e não substitui autenticação; `make run` escuta em `0.0.0.0`,
  então a API fica acessível a outras máquinas da rede, sem controle de acesso.
- Recomendado para uso em rede: proxy reverso com TLS e rate limiting (fora do escopo da v1).

## 10. Dependências

- Versões mínimas fixadas em `requirements*.txt`; revisar com `pip-audit` periodicamente.
