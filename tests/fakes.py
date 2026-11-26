"""Fakes das integrações externas (sem rede, determinísticos)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from app.core.errors import ExternalServiceError
from app.integrations.transcriber import CancelCheck, TranscribedSegment, TranscriptionResult, check_cancelled

Handler = Callable[[str], dict[str, Any]]


def default_extraction(prompt: str) -> dict[str, Any]:
    return {"historias": [
        {
            "titulo": "Emitir boletim", "perfil": "coordenador pedagógico",
            "funcionalidade": "emitir o boletim da turma", "beneficio": "acompanhar o desempenho dos alunos",
            "criterios_aceitacao": [{"titulo": "Emissão", "criterio": "Dado uma turma, quando emitir, então gera PDF."}],
            "fontes": ["F1"], "informacoes_ausentes": [], "afirmacoes_sem_evidencia": [],
        },
        {
            "titulo": "Cadastrar aluno", "perfil": None, "funcionalidade": "cadastrar novos alunos",
            "beneficio": None, "criterios_aceitacao": [], "fontes": ["F2", "F99"],
        },
    ]}


def source_texts(prompt: str) -> dict[str, str]:
    """Rótulo F → texto do trecho, lidos do bloco <fontes> do prompt."""
    block = prompt.split("<fontes>")[1].split("</fontes>")[0]
    parts = re.split(r"^\[(F\d+)\] \(.*?\)\n", block, flags=re.M)
    return {label: text.strip() for label, text in zip(parts[1::2], parts[2::2])}


def default_criteria(prompt: str) -> dict[str, Any]:
    """Dois critérios por história, citando literalmente a primeira evidência vinculada; sem evidência,
    nenhum critério e a lacuna registrada (como o prompt pede)."""
    texts = source_texts(prompt)
    result = []
    for ref, labels in re.findall(r"^\[(H\d+)\] [^\n]*\n[^\n]*\nEvidências vinculadas: ([^\n]*)$", prompt, flags=re.M):
        cited = [label.strip() for label in labels.split(",") if label.strip() in texts]
        if not cited:
            result.append({"id": ref, "criterios_aceitacao": [],
                           "lacunas_criterios": [{"elemento": "regras", "descricao": "Sem evidência para critérios."}]})
            continue
        quote = " ".join(texts[cited[0]].split()[:5])
        result.append({"id": ref, "criterios_aceitacao": [
            {"id": f"{ref}-CA1", "titulo": "Resultado principal", "dado": "o usuário autorizado",
             "quando": "ele executa a funcionalidade", "entao": "o sistema registra o resultado",
             "fontes": [{"trecho_id": cited[0], "afirmacoes_sustentadas": ["entao"], "citacao": quote}]},
            {"id": f"{ref}-CA2", "titulo": "Condição documentada", "dado": "a condição descrita na fonte",
             "quando": "o usuário confirma", "entao": "o sistema aplica a regra",
             "fontes": [{"trecho_id": cited[0], "afirmacoes_sustentadas": ["dado"], "citacao": quote}]},
        ], "lacunas_criterios": []})
    return {"historias": result, "lacunas_conjunto": []}


def default_audit(prompt: str) -> dict[str, Any]:
    """Tudo COMPATIVEL: cada componente e critério com os trechos que o prompt vinculou a ele."""
    audit = []
    for block in re.split(r"\n(?=\[H\d+\] )", prompt.split("HISTÓRIAS A AUDITAR:")[1].split("### TAREFA")[0].strip()):
        ref = re.match(r"\[(H\d+)\]", block).group(1)
        components = [
            {"componente": name, "afirmacao": "x", "resultado": "COMPATIVEL", "problemas_identificados": [],
             "evidencias_avaliadas": [{"trecho_id": l.strip(), "resultado": "COMPATIVEL", "justificativa": "ok"}
                                      for l in labels.split(",") if l.strip().startswith("F")]}
            for name, labels in re.findall(r"^- (perfil|funcionalidade|beneficio): .* \| evidências: (.*)$", block, flags=re.M)
        ]
        criteria = [
            {"criterio_id": cref, "resultado": "COMPATIVEL", "componentes_sem_sustentacao": [],
             "problemas_identificados": [],
             "evidencias_avaliadas": [{"trecho_id": l.strip(), "resultado": "COMPATIVEL", "justificativa": "ok"}
                                      for l in labels.split(",") if l.strip().startswith("F")]}
            for cref, labels in re.findall(r"^- \[(H\d+-CA\d+)\] .* \| evidências: (.*)$", block, flags=re.M)
        ]
        audit.append({"historia_id": ref, "componentes": components, "criterios_auditados": criteria})
    return {"auditoria": audit, "problemas_conjunto": []}


def default_analysis(prompt: str) -> dict[str, Any]:
    return {"analise": {
        "sistema": "Sistema escolar de boletins",
        "usuarios": [{"papel": "coordenador pedagógico", "descricao": "acompanha o desempenho", "fontes": ["F1"]}],
        "requisitos_negocio": [{"id": "RN1", "descricao": "acompanhar o desempenho dos alunos", "fontes": ["F1"]}],
        "funcionalidades": [{"id": "FN1", "usuario": "coordenador pedagógico", "descricao": "emitir o boletim da turma",
                             "requisitos_negocio": ["RN1"], "fontes": ["F1"]}],
    }}


def default_individual(prompt: str) -> dict[str, Any]:
    criteria = {k: {"resultado": "Atende", "evidencia": "ok", "acao": "Nenhuma ação necessária"} for k in (
        "well_formed", "atomic", "minimal", "conceptually_sound", "problem_oriented", "unambiguous",
        "full_sentence", "estimatable")}
    criteria["unambiguous"] = {"resultado": "Atenção", "evidencia": "Termo 'boletim' vago.", "acao": "Defina o formato."}
    return {"avaliacao_criterios": criteria, "lacunas": [{"elemento": "formato", "descricao": "Formato do boletim não definido."}]}


def default_set(prompt: str) -> dict[str, Any]:
    keys = re.findall(r"^- (conflict_free|independent|complete):", prompt, flags=re.M)
    return {"analise_transversal_conjunto": {k: {"resultado": "Atende", "observacao": "ok"} for k in keys},
            "reescrita_sugerida": "Como coordenador pedagógico, quero emitir o boletim em PDF, para acompanhar o desempenho."}


def default_questions(prompt: str) -> dict[str, Any]:
    labels = re.findall(r"^\[(D\d+)\]", prompt, flags=re.M)
    questions = [{"defeito": labels[0], "pergunta": "Qual o formato esperado?", "justificativa": "Sem evidência nas fontes."}]
    limitations = [{"defeito": labels[-1], "motivo": "Sem contexto."}] if len(labels) > 1 else []
    return {"perguntas": questions, "limitacoes": limitations}


def default_impact(prompt: str) -> dict[str, Any]:
    existing = prompt.split("HISTÓRIA EXISTENTE:")[1].split("### TAREFA")[0]
    if "boletim" in existing.lower():
        return {"relacao": "complementa", "justificativa": "Define o formato PDF.",
                "proposta": {"titulo": "Emitir boletim em PDF", "perfil": "coordenador pedagógico",
                             "funcionalidade": "emitir o boletim da turma em PDF",
                             "beneficio": "acompanhar o desempenho dos alunos", "criterios_aceitacao": []},
                "fontes": ["F1"]}
    return {"relacao": "sem_relacao", "justificativa": "", "fontes": []}


def default_split_criteria(prompt: str) -> dict[str, Any]:
    parts = re.findall(r"^- (H\d+): .*?quero (.+?),", prompt, flags=re.M)
    return {"historias": [
        {"id": h, "criterios_aceitacao": [
            {"titulo": f"Regra {n}", "criterio": f"Dado o perfil, quando {action}, então ocorre o resultado {n}."}
            for n in (1, 2, 3)
        ]} for h, action in parts
    ]}


def default_context(prompt: str) -> dict[str, Any]:
    return {"perfis": [], "objetivos": [], "termos": []}  # neutro: não altera prompts nem orçamento dos lotes


def default_consolidation(prompt: str) -> dict[str, Any]:
    return {"fundir": [], "preencher": [], "contradicoes": []}


class FakeLLM:
    def __init__(self) -> None:
        self.handlers: dict[str, Handler] = {
            "CONTEXTO_REUNIAO": default_context,
            "ANALISE_FONTES": default_analysis,
            "CONSOLIDACAO_HISTORIAS": default_consolidation,
            "GERACAO_US_QUS": default_extraction,
            "GERACAO_CRITERIOS_ACEITACAO": default_criteria,
            "VALIDACAO_EVIDENCIAS": default_audit,
            "AVALIACAO_QUS_INDIVIDUAL": default_individual,
            "AVALIACAO_QUS_CONJUNTO": default_set,
            "PERGUNTAS_FOLLOWUP": default_questions,
            "ANALISE_IMPACTO": default_impact,
            "CRITERIOS_DIVISAO": default_split_criteria,
        }
        self.calls: list[tuple[str, str]] = []
        self.schemas: dict[str, dict[str, Any] | None] = {}
        self.fail = False

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        task = re.search(r"### TAREFA: (\w+)", prompt).group(1)
        self.calls.append((task, prompt))
        self.schemas[task] = schema
        if self.fail:
            raise ExternalServiceError("LLM indisponível (simulado).")
        return self.handlers[task](prompt)

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "provedor": "fake", "modelo": "fake"}

    def prompts_for(self, task: str) -> list[str]:
        return [p for t, p in self.calls if t == task]


class FakeTranscriber:
    def __init__(self) -> None:
        self.segments = [
            TranscribedSegment(0.0, 5.0, "Precisamos emitir o boletim da turma todo bimestre."),
            TranscribedSegment(5.0, 9.5, "O coordenador acompanha o desempenho dos alunos."),
            TranscribedSegment(9.5, 14.0, "Também é preciso cadastrar novos alunos no início do ano."),
        ]
        self.error: Exception | None = None
        self.on_segment: Callable[[int], None] | None = None  # gancho: roda após cada segmento inferido
        self.processed = 0

    def transcribe(self, path: Path, should_cancel: CancelCheck | None = None) -> TranscriptionResult:
        """Espelha o adaptador real: checa o cancelamento antes de cada segmento."""
        if self.error:
            raise self.error
        self.processed = 0
        result = []
        for index, segment in enumerate(self.segments):
            check_cancelled(should_cancel)
            result.append(segment)
            self.processed += 1
            if self.on_segment:
                self.on_segment(index)
        return TranscriptionResult(14.0, "pt", result)

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "modelo": "fake"}
