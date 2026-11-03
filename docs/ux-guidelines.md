# Diretrizes de UX — Contrato da API para o frontend futuro

A v1 não tem interface gráfica. Estas diretrizes definem como a API deve se comportar para que um
frontend simples, moderno, responsivo e acessível possa ser construído sem retrabalho no backend.

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
