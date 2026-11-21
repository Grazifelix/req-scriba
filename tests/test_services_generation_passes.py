"""US-05, US-14, US-16: passada de contexto prévia e passada de consolidação, sem perder rastreabilidade.

Essas passadas só rodam no modo lotes (fontes maiores que GENERATION_BATCH_CHARS): a fixture abaixo força
uma janela de transcrição por lote.
"""

import pytest

from app.core.errors import ExternalServiceError
from app.services.consolidation import apply_operations
from app.services.generation import run_generation, start_generation
from app.services.stories import StoryService
from app.services.updates import UpdateService, run_update_analysis, start_analysis
from tests.conftest import upload_document
from tests.fakes import default_extraction


@pytest.fixture(autouse=True)
def batch_mode(container):
    container.settings.transcript_window_chars = 60  # 3 janelas de transcrição
    container.settings.generation_batch_chars = 60  # uma janela por lote


def generate(container, project_id, audio_ids=(), document_ids=()):
    with container.uow() as uow:
        job = start_generation(uow, project_id, list(audio_ids), list(document_ids), "ana")
    run_generation(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def detail(container, story_id):
    with container.uow() as uow:
        return StoryService(uow).detail(story_id)


def failing(prompt):
    raise ExternalServiceError("LLM indisponível (simulado).")


def widened_after_extraction(container, handler):
    """Lotes pequenos na extração e orçamento normal na consolidação, que agrupa as candidatas pelo mesmo
    orçamento: os lotes já estão montados quando a extração começa."""
    def wrapped(prompt):
        container.settings.generation_batch_chars = 6000
        return handler(prompt)
    return wrapped


def story(funcionalidade, perfil=None, beneficio=None, fontes=("F1",), criterios=()):
    return {"funcionalidade": funcionalidade, "perfil": perfil, "beneficio": beneficio, "fontes": list(fontes),
            "criterios_aceitacao": [{"titulo": "CA", "criterio": c} for c in criterios]}


# ---------- passada de contexto ----------

def test_context_items_are_citable_and_resolve_to_real_chunks(container, project_id, audio_id):
    container.llm.handlers["CONTEXTO_REUNIAO"] = lambda p: {
        "perfis": [{"texto": "Coordenador pedagógico acompanha o desempenho dos alunos.", "fontes": ["F1"]}]
        if "boletim" in p else [],
        "termos": [{"texto": "Item inventado sem fonte", "fontes": ["F9"]}],
    }
    container.llm.handlers["EXTRACAO_HISTORIAS"] = lambda p: {"historias": [
        story("emitir o boletim da turma", "coordenador pedagógico", "acompanhar o desempenho", fontes=["R1"]),
    ]}
    job = generate(container, project_id, [audio_id])
    assert job["resultado"]["modo"] == "lotes"
    assert job["resultado"]["contexto"] == {"itens": 1, "lotes": 3, "lotes_com_erro": []}
    prompt = container.llm.prompts_for("EXTRACAO_HISTORIAS")[0]
    assert "[R1] (perfil) Coordenador pedagógico" in prompt
    assert "Item inventado sem fonte" not in prompt  # item sem trecho válido é descartado
    created = detail(container, job["resultado"]["historias_criadas"][0])
    [evidence] = created["fontes_audio"]
    assert evidence["audio_id"] == audio_id and "boletim" in evidence["trecho"]  # texto vem do banco


def test_context_failure_does_not_fail_generation(container, project_id, audio_id):
    container.llm.handlers["CONTEXTO_REUNIAO"] = failing
    job = generate(container, project_id, [audio_id])
    assert job["status"] == "concluido" and job["resultado"]["total"] == 2
    assert len(job["resultado"]["contexto"]["lotes_com_erro"]) == 3
    assert "CONTEXTO GERAL" not in container.llm.prompts_for("EXTRACAO_HISTORIAS")[0]


def test_context_reduces_batch_budget(container, project_id, audio_id):
    from app.services.context import ContextItem, MeetingContext
    from app.services.generation import batch_budget

    container.settings.generation_batch_chars = 1000
    assert batch_budget(container, None) == 1000
    big = MeetingContext([ContextItem("R1", "perfil", "x" * 900, [])])
    assert batch_budget(container, big) == 500  # piso de metade do orçamento
    small = MeetingContext([ContextItem("R1", "perfil", "x" * 100, [])])
    assert batch_budget(container, small) == 1000 - len(small.text())


# ---------- passada de consolidação ----------

def test_consolidation_merges_fills_and_flags_conflicts(container, project_id, audio_id, document_id):
    container.llm.handlers["EXTRACAO_HISTORIAS"] = widened_after_extraction(container, lambda p: {"historias": [
        story("emitir o boletim da turma", "coordenador", "acompanhar o desempenho dos alunos", ["F1"], ["Dado uma turma, quando o coordenador emitir, então o sistema gera o PDF do boletim"]),
        story("disponibilizar as notas bimestrais", "coordenador", None, ["F1"], ["Notas publicadas ficam visíveis aos responsáveis no portal"]),
        story("cadastrar novos alunos", "secretaria", None, ["F2"]),
        story("bloquear matrículas fora do período", "secretaria", "evitar erros no cadastro", ["F2"]),
    ]})
    container.llm.handlers["CONSOLIDACAO_HISTORIAS"] = lambda p: {
        "fundir": [{"historias": ["C1", "C2"]}, {"historias": ["C3", "C4"]}],
        "preencher": [{"alvo": "C3", "campo": "benefício", "de": "C1"}, {"alvo": "C1", "campo": "perfil", "de": "C3"}],
        "contradicoes": [{"historias": ["C3", "C4"], "descricao": "Uma permite e outra bloqueia a matrícula."}],
    }
    job = generate(container, project_id, [audio_id], [document_id])
    result = job["resultado"]
    assert result["total"] == 3  # C1+C2 fundidas; C3 e C4 contraditórias não são fundidas
    assert result["consolidacao"] == {"fusoes": 1, "preenchimentos": 1, "contradicoes": 1, "grupos": 1, "erro": None}
    assert "[F" not in container.llm.prompts_for("CONSOLIDACAO_HISTORIAS")[0]  # só candidatas, sem trechos
    merged, filled, other = (detail(container, i) for i in result["historias_criadas"])
    assert merged["perfil"] == "coordenador"  # preencher nunca sobrescreve
    assert len(merged["criterios_aceitacao"]) == 2
    assert filled["beneficio"] == "acompanhar o desempenho dos alunos"  # cópia literal da candidata doadora
    assert filled["status"] == "em_revisao"
    assert filled["fontes_audio"] and filled["fontes_documentos"]  # evidências da doadora acompanham o campo
    for item in (filled, other):
        assert any(g["elemento"] == "conflito" for g in item["informacoes_ausentes"])


def test_consolidation_failure_keeps_candidates(container, project_id, audio_id):
    container.llm.handlers["EXTRACAO_HISTORIAS"] = widened_after_extraction(container, default_extraction)
    container.llm.handlers["CONSOLIDACAO_HISTORIAS"] = failing
    job = generate(container, project_id, [audio_id])
    assert job["status"] == "concluido" and job["resultado"]["total"] == 2
    assert "simulado" in job["resultado"]["consolidacao"]["erro"]


def test_passes_can_be_disabled(container, project_id, audio_id):
    container.settings.generation_context_pass = False
    container.settings.generation_consolidation = False
    job = generate(container, project_id, [audio_id])
    assert job["resultado"]["contexto"] is None and job["resultado"]["consolidacao"] is None
    assert not container.llm.prompts_for("CONTEXTO_REUNIAO")
    assert not container.llm.prompts_for("CONSOLIDACAO_HISTORIAS")


def test_merge_never_joins_transitively_conflicting_candidates():
    def candidate(name):
        return {"funcionalidade": name, "perfil": None, "beneficio": None, "criterios_aceitacao": [],
                "evidencias": [{"trecho_id": name}], "informacoes_ausentes": [], "afirmacoes_sem_evidencia": []}

    labeled = {"C1": candidate("a"), "C2": candidate("b"), "C3": candidate("c")}
    operations = {"fundir": [{"historias": ["C1", "C2", "C3"]}], "preencher": [],
                  "contradicoes": [{"historias": ["C1", "C3"], "descricao": "conflito"}]}
    summary = {"fusoes": 0, "preenchimentos": 0, "contradicoes": 0}
    result = apply_operations(labeled, operations, summary)
    assert [c["funcionalidade"] for c in result] == ["a", "c"]
    assert [e["trecho_id"] for e in result[0]["evidencias"]] == ["a", "b"]
    assert summary["fusoes"] == 1


# ---------- análise de novas fontes ----------

NEW_DOC = (
    "Ata da segunda entrevista: o boletim será disponibilizado em PDF para download.\n\n"
    "Os responsáveis querem receber notificações por e-mail quando houver faltas."
)


@pytest.fixture
def existing_story(container, project_id, audio_id):
    return generate(container, project_id, [audio_id])["resultado"]["historias_criadas"][0]


def test_new_requirements_use_context_and_consolidation(container, project_id, existing_story):
    container.llm.handlers["CONTEXTO_REUNIAO"] = lambda p: {
        "perfis": [{"texto": "Responsáveis acompanham a frequência dos alunos.", "fontes": ["F1"]}],
    }
    container.llm.handlers["EXTRACAO_HISTORIAS"] = widened_after_extraction(container, lambda p: {"historias": [
        story("receber notificações de faltas por e-mail", "responsável", "acompanhar a frequência"),
        story("ser avisado quando o filho faltar", "responsável", None),
    ]})
    container.llm.handlers["CONSOLIDACAO_HISTORIAS"] = lambda p: {"fundir": [{"historias": ["C1", "C2"]}]}
    container.settings.chunk_size, container.settings.chunk_overlap = 50, 5  # vários lotes sem correspondência
    new_doc = upload_document(container, project_id, NEW_DOC, "ata2.txt")
    with container.uow() as uow:
        job = start_analysis(uow, project_id, [], [new_doc], "ana")
    run_update_analysis(container, job["id"])
    with container.uow() as uow:
        result = uow.jobs.get(job["id"])["resultado"]
        items = UpdateService(uow).list(project_id)["itens"]
    assert result["contexto"]["itens"] == 1 and result["consolidacao"]["fusoes"] == 1
    assert "[R1] (perfil)" in container.llm.prompts_for("EXTRACAO_HISTORIAS")[-1]
    new_req = [s for s in items if s["tipo"] == "novo_requisito"]
    assert len(new_req) == 1
