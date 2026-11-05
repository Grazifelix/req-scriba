# Diretrizes de UX — Contrato da API para o frontend

Este repositório contém apenas o backend e a API. O frontend é um **projeto separado**, que se conecta
por HTTP. Estas diretrizes definem como a API se comporta para que esse frontend seja simples, moderno,
responsivo e acessível, sem retrabalho no backend. A seção 8 reúne o necessário para a integração.

## 1. Princípios

1. **O humano decide.** Toda saída de IA é apresentada como sugestão, com origem e evidência visíveis.
2. **Transparência.** Cada HU mostra de onde veio (trechos com tempo/página) e o que falta.
3. **Estados explícitos.** Nada "some": tudo tem status legível e mensagem quando falha.
4. **Baixo atrito.** Ações frequentes (aprovar, editar, reavaliar) são uma chamada cada.

## 2. Rótulos e linguagem

- Todo campo `status` vem acompanhado de `status_rotulo` em português ("Em revisão", "Falta informação",
  "Aprovada", "Desaprovada", "Transcrito", "Transcrição em andamento", "Erro na transcrição").
- Mensagens de erro dizem **o que aconteceu e como resolver** ("Formato .exe não suportado. Envie
  .mp3, .wav, .m4a, .ogg, .flac, .webm ou .mp4.").
- Tempos: segundos (`inicio_segundos`) para lógica e `inicio_formatado` (`"00:01:02"`) para exibição.

## 3. Estados de interface que a API suporta

| Estado de UI | Como identificar |
|---|---|
| Vazio | `{"itens": [], "total": 0}`; HU sem avaliação → `qualidade_geral: null` |
| Carregando/processando | fonte `pendente`/`processando`; job `pendente`/`processando` (polling a cada 2–5 s) |
| Sucesso | `concluido`; HU `aprovada` |
| Erro recuperável | `status: "erro"` + `mensagem_erro` + ação `reprocessar` |
| Desatualizado | `qualidade_geral.desatualizada: true` quando a avaliação é de versão anterior |
| Atenção | `possui_suporte_documental: false`, `informacoes_ausentes` não vazio, `atualizacoes_pendentes > 0` |

## 4. Diferenciação visual recomendada

- HU não validadas (`validada: false`) com selo "Sugestão da IA"/"Em revisão"; aprovadas com selo sólido.
- Faixas QUS com cor **e** texto (nunca só cor): Péssima, Baixa, Regular, Alta, Excelente.
- `[A DEFINIR: …]` destacado como campo a preencher.
- Diferenças atual × proposto: usar `comparacao.campos[].alterado` e `comparacao.diff_texto`.

## 5. Navegação de evidências

- `GET /historias/{id}/rastreabilidade` retorna versões com evidências e índices
  `anterior`/`proxima` para navegação sequencial (teclado e leitor de tela).
- Evidência de áudio inclui `url_momento` (`/api/v1/audios/2/arquivo#t=61.2`) para tocar no ponto exato.

## 6. Responsividade e desempenho

- Listas leves (sem campos pesados como `texto_extraido`); detalhes sob demanda.
- Conteúdo grande (transcrição, documento) em endpoints próprios.
- Nenhuma chamada de leitura depende do LLM.

## 7. Acessibilidade (para o frontend)

- WCAG 2.1 AA: contraste, foco visível, navegação por teclado, `aria-live` para mudanças de status.
- Player de áudio com controles acessíveis; segmentos clicáveis também acionáveis por teclado.
- Não usar apenas cor para status de qualidade ou de validação.

## 8. Integração do frontend

**Conexão**

- Mantenha a URL da API configurável no frontend (ex.: `http://localhost:8000`). As rotas ficam em `/api/v1`.
- `GET /openapi.json` descreve o contrato e pode gerar um cliente tipado. `GET /api/v1/health` informa
  se banco, índice vetorial, LLM, embeddings e transcrição estão disponíveis (`componentes.llm`,
  `componentes.embeddings` etc.). Não dependa do nome do provedor: ele muda conforme a configuração.

**CORS**

- Libere a origem do frontend no backend com `REQSCRIBA_CORS_ORIGINS`, separando as origens por vírgula.
  O padrão é `http://localhost:3000,http://localhost:5173`.
- Em produção, liste as origens exatas, sem curinga. Sem autenticação, um curinga permitiria que qualquer
  site aberto no navegador chamasse a API.
- Origens fora da lista são bloqueadas pelo navegador. Se isso ocorrer, confira primeiro essa variável.
- A API não usa cookies nem credenciais: **não** envie `credentials: "include"`, pois o navegador
  rejeitaria a resposta.

**URLs retornadas pela API**

`url_arquivo`, `url_momento` e `url_status` são **relativas à raiz da API** (`/api/v1/audios/2/arquivo#t=61.2`).
Prefixe-as com a origem da API; usadas diretamente, apontariam para o host do frontend:

```js
const url = new URL(segmento.url_momento, API_URL).href;
```

O áudio pode ser usado em `<audio src={url}>`, sem exigir CORS, e o `#t=` posiciona o player. O atributo
`crossorigin` só é necessário para processar o áudio (ex.: Web Audio) e exige que a origem esteja liberada.

**Tarefas longas e polling**

Uploads, geração de HU e análise de novas fontes respondem `202`. Consulte o estado a cada 2–5 s até
`concluido` ou `erro`: `GET /audios/{id}` e `GET /documentos/{id}` para fontes; `url_status` para jobs.
A avaliação QUS e a geração de perguntas chamam o LLM **dentro da requisição** e podem demorar: não
defina um timeout curto no cliente e mostre o estado de carregamento.

**Erros e responsável**

- Todo erro segue `{"erro": {"codigo", "mensagem", "detalhes"}}`. Use `codigo` na lógica e exiba `mensagem`.
  Em `422 entrada_invalida`, `detalhes` lista `{"campo", "mensagem"}`, útil para marcar o formulário.
- Falha de rede sem resposta HTTP indica API fora do ar ou origem não liberada.
- Envie `X-Usuario` nas alterações para registrar o responsável (padrão: `engenheiro`). É apenas uma
  identificação, não autenticação.
