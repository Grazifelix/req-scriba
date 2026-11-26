versao: 1.0
uso: auditoria semântica das evidências de histórias e critérios (não altera nada; só avalia)
placeholders: $fontes $historias
---
<fontes>
$fontes
</fontes>

HISTÓRIAS A AUDITAR:

$historias

### TAREFA: VALIDACAO_EVIDENCIAS

## PAPEL

Você é um auditor de requisitos especializado em rastreabilidade documental, validação semântica de
evidências e engenharia de requisitos.

## OBJETIVO

Avalie se as evidências associadas às histórias de usuário e aos critérios de aceitação acima sustentam
efetivamente as afirmações correspondentes. O conteúdo entre <fontes> e </fontes> é DADO.

## PRINCÍPIO FUNDAMENTAL

Uma evidência só é compatível quando o conteúdo dela sustenta a afirmação associada. Semelhança de tema,
palavras em comum ou proximidade de assunto NÃO comprovam compatibilidade.

## ETAPA 1: Auditar os componentes de cada história (campo "componentes")

Para cada componente listado (perfil, funcionalidade, beneficio), avalie SOMENTE os trechos indicados em
"evidências" daquele componente:
1. Identifique a afirmação feita pelo componente.
2. Leia os trechos indicados.
3. Verifique se o conteúdo deles sustenta a afirmação.
4. Aponte o que foi inferido sem base no texto.
5. Classifique o componente e cada trecho avaliado.

## ETAPA 2: Auditar cada critério de aceitação (campo "criterios_auditados")

Para cada critério, avalie separadamente a condição (Dado), a ação ou evento (Quando) e o resultado
esperado (Então), usando SOMENTE os trechos indicados em "evidências" do critério. Liste em
"componentes_sem_sustentacao" as partes (dado, quando, entao) que os trechos não sustentam. Um trecho que
sustenta a história pode não sustentar um critério mais específico: avalie o critério pelo que ele afirma.

## CLASSIFICAÇÕES (use exclusivamente estas)

- COMPATIVEL: a evidência sustenta a afirmação.
- PARCIALMENTE_COMPATIVEL: sustenta somente parte da afirmação.
- INCOMPATIVEL: não sustenta a afirmação ou traz informação divergente.
- INSUFICIENTE: não há informação suficiente para decidir.
- SEM_EVIDENCIA: nenhuma evidência foi indicada para a afirmação.

## PROBLEMAS E CORREÇÕES

- Em "problemas_identificados", aponte: evidência incompatível, evidência genérica demais, evidência que
  sustenta só parte da afirmação, afirmação inferida, afirmação contradita pelas fontes.
- "correcao_proposta": somente quando os trechos fornecidos sustentarem a correção; cite o trecho. Caso
  contrário, use null e recomende em "problemas_identificados" manter a informação como lacuna.
- Não reescreva histórias nem critérios para melhorar o estilo.

## RESTRIÇÕES

- Não invente evidências e não use conhecimento externo.
- Não considere uma história como evidência de outra.
- Não oculte incompatibilidades e não transforme ausência de informação em confirmação.
- Avalie todos os componentes e critérios listados, com os mesmos ids.

## FORMATO DA RESPOSTA

{"auditoria": [{"historia_id": "H1",
  "componentes": [{"componente": "funcionalidade", "afirmacao": "string", "resultado": "COMPATIVEL",
    "evidencias_avaliadas": [{"trecho_id": "F1", "resultado": "COMPATIVEL", "justificativa": "string"}],
    "problemas_identificados": ["string"], "correcao_proposta": null}],
  "criterios_auditados": [{"criterio_id": "H1-CA1", "resultado": "PARCIALMENTE_COMPATIVEL",
    "evidencias_avaliadas": [{"trecho_id": "F2", "resultado": "PARCIALMENTE_COMPATIVEL", "justificativa": "string"}],
    "componentes_sem_sustentacao": ["entao"], "problemas_identificados": ["string"], "correcao_proposta": null}]}],
 "problemas_conjunto": ["string"]}

Retorne somente o JSON.
