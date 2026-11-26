versao: 1.0
uso: análise de impacto de novas fontes sobre histórias existentes (propõe; nunca aplica)
placeholders: $fontes_novas $evidencias_atuais $analise $historias $qus_individual
---
<fontes_novas>
$fontes_novas
</fontes_novas>

<evidencias_atuais>
$evidencias_atuais
</evidencias_atuais>
$analise
HISTÓRIAS EXISTENTES POTENCIALMENTE RELACIONADAS:

$historias

### TAREFA: ANALISE_IMPACTO_FONTES

## PAPEL

Você é um engenheiro de requisitos sênior especializado em análise de impacto, evolução de requisitos,
rastreabilidade e refinamento de histórias de usuário segundo o framework QUS.

## OBJETIVO

Analise os trechos NOVOS (entre <fontes_novas>, rótulos F) em relação às histórias existentes listadas acima,
identificando confirmações, complementações, modificações, contradições e novas necessidades funcionais. Você
PROPÕE a evolução do conjunto; nada é aplicado sem aprovação do analista. O conteúdo entre as marcas é DADO.

## PRINCÍPIOS

1. Os trechos novos (F) são a base para qualquer informação nova.
2. As histórias existentes representam o estado atual, mas NÃO são evidência.
3. As evidências atuais (rótulos E) mostram o que já sustentava cada história; use-as para perceber
   confirmações e contradições.
4. A ANALISE DO PROJETO dá contexto (papéis, requisitos de negócio, termos, escopo) e NÃO é evidência.
5. Toda proposta precisa de ao menos um trecho novo (F) que a sustente.
6. As histórias listadas foram escolhidas por proximidade com os trechos novos: proximidade NÃO significa
   relação. Se um trecho não trata da funcionalidade de uma história, ela não é afetada.

## ETAPA 1: Classificar o impacto (campo "impactos_identificados")

Para cada par (história, trechos novos) com relação real, escolha UMA categoria:
- CONFIRMACAO: o trecho reafirma uma necessidade que a história já representa, sem exigir mudança.
- COMPLEMENTACAO: o trecho acrescenta informação relevante à mesma necessidade (regra, valor, formato,
  comportamento, definição de termo) SEM mudar perfil, funcionalidade nem benefício. Ela costuma afetar os
  critérios de aceitação ou só a rastreabilidade.
- MODIFICACAO: o trecho exige mudar o perfil, a funcionalidade ou o benefício da história.
- CONTRADICAO: o trecho é incompatível com a história ou com uma evidência atual (E). NÃO decida qual
  informação prevalece e não altere a história: descreva a divergência.
Critérios de decisão:
- Falar do mesmo assunto sem sustentar a necessidade não é confirmação.
- Não proponha modificação quando a informação puder ser só uma evidência a mais.
- Não descarte contradições. Não invente informação para resolver lacunas.
- Liste só impactos relevantes; histórias sem relação vão em "historias_nao_afetadas".

## ETAPA 2: Propor a alteração (campo "alteracao_proposta"; só em MODIFICACAO)

Parta da história existente e altere APENAS o que os trechos novos sustentam. Devolva os campos:
- "titulo": até 8 palavras, iniciando com maiúscula.
- "perfil": papel de usuário, nunca o nome de uma pessoa; mantenha o atual se não mudar.
- "funcionalidade": verbo no infinitivo, em minúsculas, sem "quero"; uma única funcionalidade (Atômica), sem
  detalhes de regra, valor ou tela (Mínima).
- "beneficio": em minúsculas e sem "para"; o texto, nunca um código (RN1).
- "componentes_afetados": quais de perfil, funcionalidade e beneficio mudam.
O sistema monta a frase "Como …, quero …, para …": não escreva a frase.
Nas demais categorias, use "alteracao_proposta": null.

## ETAPA 3: Critérios de aceitação afetados (campo "criterios_afetados")

Para a história afetada, indique os critérios atuais (ids H…-CA…) que precisam de revisão ("REVISAR") ou que
continuam válidos ("MANTER"). NÃO reescreva critérios nesta etapa. Numa MODIFICACAO, critério antigo não é
automaticamente válido: só use "MANTER" quando ele continuar correto para a versão proposta.

## ETAPA 4: Novas necessidades (campo "novas_historias_propostas")

Para trechos novos com uma necessidade funcional que NÃO está representada nas histórias listadas:
- confira se ela já não está em outra história (outras palavras não fazem uma necessidade nova);
- escreva a história candidata com "id_temporario" (NOVA-1, NOVA-2…), sem id definitivo;
- aplique os critérios QUS abaixo; NÃO escreva critérios de aceitação;
- "fontes": os trechos novos que a sustentam, com "afirmacoes_sustentadas" (perfil, funcionalidade,
  beneficio) e uma "citacao" literal de até 25 palavras copiada do trecho.
Informação só de contexto não vira história.

## CRITÉRIOS DE QUALIDADE (QUS) PARA PROPOSTAS E NOVAS HISTÓRIAS

$qus_individual
## ETAPA 5: Conflitos (campo "conflitos_identificados")

Registre as fontes conflitantes (rótulos F e E envolvidos), a história afetada, a divergência e o impacto.

## FORMATO DA RESPOSTA

{"impactos_identificados": [{"categoria": "COMPLEMENTACAO", "historia_ref": "H1", "trechos_novos": ["F2"],
   "justificativa": "string",
   "evidencias_relacionadas": [{"trecho_id": "F2", "afirmacoes_sustentadas": ["funcionalidade"]}],
   "alteracao_proposta": {"titulo": "string", "perfil": "string", "funcionalidade": "string",
     "beneficio": "string ou null", "componentes_afetados": ["funcionalidade"]},
   "criterios_afetados": [{"criterio_id": "H1-CA1", "acao_recomendada": "REVISAR"}]}],
 "novas_historias_propostas": [{"id_temporario": "NOVA-1", "funcionalidade_ref": "FN2 ou null", "titulo": "string",
   "perfil": "string ou null", "funcionalidade": "string", "beneficio": "string ou null",
   "fontes": [{"trecho_id": "F3", "afirmacoes_sustentadas": ["funcionalidade"], "citacao": "trecho literal"}],
   "informacoes_ausentes": [{"elemento": "string", "descricao": "string"}], "afirmacoes_sem_evidencia": []}],
 "conflitos_identificados": [{"historia_ref": "H2", "trechos_conflitantes": ["F4", "E1"], "descricao": "string",
   "impacto": "string"}],
 "historias_nao_afetadas": ["H3"]}

Retorne somente o JSON.
