"""Fakes das integrações externas (sem rede, determinísticos)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from app.core.errors import ExternalServiceError
from app.integrations.transcriber import TranscribedSegment, TranscriptionResult

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


def default_individual(prompt: str) -> dict[str, Any]:
    criteria = {k: {"resultado": "Atende", "evidencia": "ok", "acao": "Nenhuma ação necessária"} for k in (
        "well_formed", "atomic", "minimal", "conceptually_sound", "problem_oriented", "unambiguous",
        "full_sentence", "estimatable")}
    criteria["unambiguous"] = {"resultado": "Atenção", "evidencia": "Termo 'boletim' vago.", "acao": "Defina o formato."}
    return {"avaliacao_criterios": criteria, "lacunas": [{"elemento": "formato", "descricao": "Formato do boletim não definido."}],
            "reescrita_sugerida": "Como coordenador pedagógico, quero emitir o boletim em PDF, para acompanhar o desempenho."}


def default_set(prompt: str) -> dict[str, Any]:
    keys = re.findall(r"^- (conflict_free|independent|complete):", prompt, flags=re.M)
    return {"analise_transversal_conjunto": {k: {"resultado": "Atende", "observacao": "ok"} for k in keys}}


def default_questions(prompt: str) -> dict[str, Any]:
    labels = re.findall(r"^\[(D\d+)\]", prompt, flags=re.M)
    questions = [{"defeito": labels[0], "pergunta": "Qual o formato esperado?", "justificativa": "Sem evidência nas fontes."}]
    limitations = [{"defeito": labels[-1], "motivo": "Sem contexto."}] if len(labels) > 1 else []
    return {"perguntas": questions, "limitacoes": limitations}


def default_impact(prompt: str) -> dict[str, Any]:
    if "boletim" in prompt.split("NOVOS TRECHOS:")[0].lower():
        return {"relacao": "complementa", "justificativa": "Define o formato PDF.",
                "proposta": {"titulo": "Emitir boletim em PDF", "perfil": "coordenador pedagógico",
                             "funcionalidade": "emitir o boletim da turma em PDF",
                             "beneficio": "acompanhar o desempenho dos alunos", "criterios_aceitacao": []},
                "fontes": ["F1"]}
    return {"relacao": "sem_relacao", "justificativa": "", "fontes": []}


class FakeLLM:
    def __init__(self) -> None:
        self.handlers: dict[str, Handler] = {
            "EXTRACAO_HISTORIAS": default_extraction,
            "AVALIACAO_QUS_INDIVIDUAL": default_individual,
            "AVALIACAO_QUS_CONJUNTO": default_set,
            "PERGUNTAS_FOLLOWUP": default_questions,
            "ANALISE_IMPACTO": default_impact,
        }
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    def generate_json(self, system: str, prompt: str) -> dict[str, Any]:
        task = re.search(r"### TAREFA: (\w+)", prompt).group(1)
        self.calls.append((task, prompt))
        if self.fail:
            raise ExternalServiceError("Ollama indisponível (simulado).")
        return self.handlers[task](prompt)

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

    def transcribe(self, path: Path) -> TranscriptionResult:
        if self.error:
            raise self.error
        return TranscriptionResult(14.0, "pt", list(self.segments))

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "modelo": "fake"}
