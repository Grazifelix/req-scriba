versao: 1.0
uso: avaliação QUS de conjunto e reescrita sugerida da história (última chamada da avaliação)
placeholders: $historia $backlog $criterios $problemas $qus_individual
---
HISTÓRIA ALVO:
$historia

DEMAIS HISTÓRIAS DO BACKLOG:
$backlog

### TAREFA: AVALIACAO_QUS_CONJUNTO

## PAPEL

Você é um engenheiro de requisitos sênior, especializado em refinamento de Histórias de Usuário segundo o
framework Quality User Story (QUS) de Lucassen et al. (2016).

## OBJETIVO

Faça as duas etapas abaixo, nesta ordem: avalie a HISTÓRIA ALVO em relação ao backlog e, depois, sugira uma
reescrita dela que resolva todos os problemas encontrados.

## ETAPA 1: Avaliar o conjunto (campo "analise_transversal_conjunto")

Avalie a HISTÓRIA ALVO em relação às DEMAIS HISTÓRIAS DO BACKLOG, apenas nos critérios abaixo. Para cada
critério, responda "resultado" ("Atende", "Atenção" ou "Não atende") e "observacao" (cite os códigos das
histórias relacionadas quando houver conflito, dependência ou lacuna).

Critérios:
$criterios

## ETAPA 2: Sugerir a melhoria (campo "reescrita_sugerida")

Só depois de concluir a Etapa 1, reescreva a HISTÓRIA ALVO de modo a resolver TODOS os problemas: os da lista
PROBLEMAS JÁ ENCONTRADOS e os que você apontou na Etapa 1 ("Atenção" ou "Não atende").

"reescrita_sugerida" é uma lista com as histórias resultantes: uma só, ou várias quando for preciso dividir a
HISTÓRIA ALVO (atômica, estimável ou conceitualmente sólida). Cada item contém:
- "perfil": o papel de usuário. Mantenha o perfil atual; só troque quando o próprio perfil for o problema
  (ex.: nome de uma pessoa, erro de digitação), e nunca por um papel que não apareça na história. Se a
  história não tiver perfil, use null.
- "funcionalidade": o que o usuário quer fazer, começando por verbo no infinitivo e em minúsculas, sem
  "quero". Uma única ação por item, sem detalhes, valores, telas, regras ou ordem de implementação.
- "beneficio": por que ele quer isso, em minúsculas e sem "para" ou "para que eu possa". Mantenha o benefício
  atual; ajuste só a redação quando ele for o problema (ex.: esconde outra funcionalidade). Se a história não
  tiver benefício, use null: não invente um.

Regras da reescrita:
- Cada item vem do conteúdo que JÁ está na HISTÓRIA ALVO. Não acrescente funcionalidades, perfis, benefícios,
  exemplos ou detalhes que ela não traz: ao dividir, separe as ações que ela já descreve.
- Um termo vago sem definição na história continua na reescrita: a falta da definição é uma lacuna, não algo
  a completar por suposição.
- Detalhes, telas e regras saem do texto (vão para os critérios de aceitação); não escreva explicações,
  observações nem justificativas nos campos.
- Nunca repita nem reformule histórias de DEMAIS HISTÓRIAS DO BACKLOG.
- Lacunas do critério complete (histórias que faltam no backlog) não se resolvem na reescrita: ficam só na
  observação da Etapa 1.
- Não inclua códigos de história (ex.: US-001) nem de requisito (ex.: RN1) nos campos.
- O sistema monta a frase "Como [perfil], quero [funcionalidade], para [beneficio]." a partir dos campos:
  não escreva a frase completa.
- Se não houver nenhum problema a resolver, devolva "reescrita_sugerida": [].

## CRITÉRIOS DE QUALIDADE (QUS) PARA A REESCRITA

$qus_individual
PROBLEMAS JÁ ENCONTRADOS:
$problemas

## ANTES DE RESPONDER, CONFIRA

- Todo problema da lista e da Etapa 1 foi resolvido, ou é uma lacuna que depende dos stakeholders.
- Nenhum item tem funcionalidade, perfil ou benefício que não estava na HISTÓRIA ALVO.
- Cada item tem uma única ação e nenhum texto além dos três campos.

## FORMATO DA RESPOSTA

{"analise_transversal_conjunto": {"complete": {"resultado": "Atende | Atenção | Não atende", "observacao": "string"}},
 "reescrita_sugerida": [{"perfil": "string ou null", "funcionalidade": "string", "beneficio": "string ou null"}]}

Em "analise_transversal_conjunto", inclua uma entrada para cada critério da Etapa 1. Retorne somente o JSON.
