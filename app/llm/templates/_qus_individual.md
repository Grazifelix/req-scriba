versao: 1.0
uso: bloco compartilhado (GERACAO_US_QUS, ANALISE_IMPACTO)
---
Individuais (aplique a cada história ao escrevê-la):
1. Bem formada: tem perfil, funcionalidade e o benefício.
2. Atômica: expressa exatamente requisito para uma única funcionalidade, sem agrupar recursos em um único texto.
   Incorreto: "Como usuário, posso clicar em um local específico no mapa e, assim, realizar uma busca por pontos de interesse associados a essa combinação de latitude e longitude."
   Correto: H1 "Como usuário, posso clicar em um local específico no mapa"; H2 "Como usuário, posso visualizar pontos de referência associados à combinação de latitude e longitude de um local específico."
3. Mínima: contém apenas perfil, funcionalidade e benefício. Detalhes, observações, telas, regras e ordem de
   implementação DEVEM ficar fora do texto da história (são tratados na etapa de critérios de aceitação).
   Incorreto: "Como um profissional de saúde, quero visualizar as horas registradas esta semana (divididas por produtos e atividades). Veja: protótipo da Alice. OBS: primeiro crie a tela de visão geral e, depois, adicione as validações."
   Correto: "Como um profissional de saúde, eu quero visualizar as horas registradas esta semana."
4. Conceitualmente sólida: a funcionalidade é o meio e o benefício é o motivo; o benefício não pode
   esconder outra funcionalidade.
   Incorreto: H1 "Como usuário, quero abrir o mapa interativo para visualizar a localização de pontos de referência." - funcionalidade "abrir o mapa", benefício "ver a localização dos estabelecimentos"
   Correto: H1 "Como usuário, quero abrir o mapa interativo"; H2 "Como usuário, quero ver a localização de pontos de referência no mapa interativo."
5. Orientada ao problema: descreve a necessidade, não a solução técnica.
   Incorreto: "Como profissional de assistência, quero salvar um reembolso — adicionar botão de salvar no canto superior direito (nunca desabilitado)."
   Correto: "Como profissional de saúde, quero garantir o reembolso."
6. Sem ambiguidade: evite termos vagos ("conteúdo", "dados", "gerenciar", "melhorar"). Se a fonte usar um
   termo vago sem defini-lo, mantenha o termo e registre a falta da definição em "informacoes_ausentes".
   Incorreto: "Como usuário, posso editar o conteúdo que adicionei à página de perfil de uma pessoa."
   Correto (se a fonte disser): "Como usuário, posso editar conteúdo de vídeo, foto e áudio que adicionei à página de perfil de uma pessoa."
7. Frase completa: a funcionalidade é uma ação clara, não um rótulo solto.
   Incorreto: "configuração do servidor"
   Correto: "Como administrador, quero configurar o arquivo sudoers do servidor"
8. Estimável: não pode ser tão ampla que impeça estimar o esforço; divida histórias amplas.
   Incorreto: H1 "Como profissional de assistência, quero visualizar minha lista de rotas para os próximos dias, para que eu possa me preparar (por exemplo, saber a que horas devo começar o deslocamento)."
   Correto: H1 "Como profissional de assistência, quero visualizar minha lista de rotas para os próximos dias/dias futuros, para que eu possa me preparar"; H2 "Como gestor, quero carregar uma lista de rotas para profissionais de assistência."
