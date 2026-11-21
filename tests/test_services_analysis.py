"""Análise das fontes: chamada única, análise gravada e rastreável, revisão humana (CRUD) e reuso no prompt."""

import pytest

from app.core.errors import BusinessValidationError, NotFoundError
from app.services.analysis import AnalysisService
from app.services.generation import run_generation, start_generation
from app.services.stories import StoryService


def generate(container, project_id, audio_ids=(), document_ids=()):
    with container.uow() as uow:
        job = start_generation(uow, project_id, list(audio_ids), list(document_ids), "ana")
    run_generation(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def detail(container, story_id):
    with container.uow() as uow:
        return StoryService(uow).detail(story_id)


def analysis_detail(container, analysis_id):
    with container.uow() as uow:
        return AnalysisService(uow).detail(analysis_id)


def items_of(analysis, kind):
    return [i for i in analysis["itens"] if i["tipo"] == kind]


def full_response(prompt):
    """Resposta no formato novo. F1 = transcrição, F2 = documento; FN2 e F9 não são rótulos válidos."""
    return {
        "analise": {
            "sistema": "Sistema escolar de boletins",
            "participantes": [{"nome": "Ana", "papel_na_reuniao": "diretora", "fontes": ["F1"]}],
            "usuarios": [{"papel": "coordenador pedagógico", "descricao": "acompanha o desempenho", "fontes": ["F1"]}],
            "requisitos_negocio": [{"id": "RN1", "descricao": "acompanhar o desempenho dos alunos", "fontes": ["F1"]}],
            "funcionalidades": [{"id": "FN1", "usuario": "coordenador pedagógico", "descricao": "emitir o boletim",
                                 "requisitos_negocio": ["RN1"], "fontes": ["F1", "FN2"]}],
            "fora_de_escopo": [{"descricao": "aprovar o cronograma", "motivo": "tarefa do projeto", "fontes": ["F9"]}],
        },
        "historias": [
            {"id": "H1", "funcionalidade_ref": "FN1", "titulo": "Emitir boletim", "perfil": "coordenador pedagógico",
             "funcionalidade": "Emitir o boletim da turma", "beneficio": "acompanhar o desempenho dos alunos",
             "fontes": ["F1", "FN2"], "citacao": "Precisamos emitir o boletim da turma todo bimestre"},
            {"id": "H2", "funcionalidade_ref": "FN1", "titulo": "Publicar boletim", "perfil": "coordenador pedagógico",
             "funcionalidade": "publicar o boletim no portal", "fontes": ["F1"],
             "citacao": "o boletim será publicado no portal"},
        ],
        "lacunas_conjunto": [{"tipo": "conflito", "historias": ["H1", "H2"], "descricao": "Canal de entrega divergente."}],
    }


# ---------- chamada única ----------

def test_single_call_reads_all_sources_without_extra_passes(container, project_id, audio_id, document_id):
    job = generate(container, project_id, [audio_id], [document_id])
    result = job["resultado"]
    assert result["modo"] == "chamada_unica" and result["lotes"] == 1
    assert result["contexto"] is None and result["consolidacao"] is None
    assert not container.llm.prompts_for("CONTEXTO_REUNIAO") and not container.llm.prompts_for("CONSOLIDACAO_HISTORIAS")
    [prompt] = container.llm.prompts_for("EXTRACAO_HISTORIAS")
    assert "TODAS as fontes" in prompt and "documento escopo.txt" in prompt
    assert prompt.index("</fontes>") < prompt.index("### TAREFA") < prompt.index("FORMATO DA RESPOSTA")


def test_generation_saves_traceable_analysis(container, project_id, audio_id, document_id):
    container.llm.handlers["EXTRACAO_HISTORIAS"] = full_response
    result = generate(container, project_id, [audio_id], [document_id])["resultado"]
    first, second = (detail(container, i) for i in result["historias_criadas"])

    assert first["funcionalidade"] == "emitir o boletim da turma"  # minúscula inicial
    assert first["fontes_audio"] and not first["fontes_documentos"]  # "FN2" não vira a fonte F2
    assert not any("Citação" in c for c in first["afirmacoes_sem_evidencia"])  # citação literal conferida
    assert any("Citação não encontrada" in c for c in second["afirmacoes_sem_evidencia"])
    for story in (first, second):
        assert any(g["elemento"] == "conflito" for g in story["informacoes_ausentes"])  # lacuna de conjunto

    analysis = analysis_detail(container, result["analise_id"])
    assert analysis["sistema"] == "Sistema escolar de boletins" and analysis["origem"] == "ia"
    assert analysis["revisada"] is False and analysis["job_id"]
    assert {f["nome_arquivo"] for f in analysis["fontes"]} == {"reuniao.wav", "escopo.txt"}
    assert analysis["totais"] == {"participante": 1, "usuario": 1, "requisito_negocio": 1,
                                  "funcionalidade": 1, "fora_de_escopo": 1}
    [functionality] = items_of(analysis, "funcionalidade")
    assert functionality["codigo"] == "FN1" and functionality["referencias"] == ["RN1"]
    assert functionality["complemento"] == "coordenador pedagógico"
    assert functionality["historia_ids"] == result["historias_criadas"]
    [evidence] = functionality["evidencias"]
    assert evidence["tipo_fonte"] == "audio" and "boletim" in evidence["texto"]  # texto vem do banco
    [out_of_scope] = items_of(analysis, "fora_de_escopo")
    assert out_of_scope["complemento"] == "tarefa do projeto" and out_of_scope["possui_evidencia"] is False


def test_old_format_response_saves_no_analysis(container, project_id, audio_id):
    result = generate(container, project_id, [audio_id])["resultado"]
    assert result["total"] == 2 and result["analise_id"] is None


# ---------- modo lotes ----------

def test_batches_merge_analysis_and_renumber_codes(container, project_id):
    from tests.conftest import upload_audio

    container.settings.transcript_window_chars = 60
    container.settings.generation_batch_chars = 60
    audio_id = upload_audio(container, project_id)

    def per_batch(prompt):
        sources = prompt.split("</fontes>")[0]
        user = {"papel": "coordenador pedagógico", "fontes": ["F1"]}
        if "boletim" in sources:
            need, goal, action = "emitir o boletim", "acompanhar o desempenho", "emitir o boletim da turma"
        elif "cadastrar" in sources:
            need, goal, action = "cadastrar novos alunos", "manter o cadastro atualizado", "cadastrar novos alunos"
        else:
            return {"analise": {"usuarios": [user]}, "historias": []}
        return {
            "analise": {"usuarios": [user],
                        "requisitos_negocio": [{"id": "RN1", "descricao": goal, "fontes": ["F1"]}],
                        "funcionalidades": [{"id": "FN1", "descricao": need, "requisitos_negocio": ["RN1"], "fontes": ["F1"]}]},
            "historias": [{"id": "H1", "funcionalidade_ref": "FN1", "perfil": "coordenador pedagógico",
                           "funcionalidade": action, "fontes": ["F1"]}],
        }

    container.llm.handlers["EXTRACAO_HISTORIAS"] = per_batch
    result = generate(container, project_id, [audio_id])["resultado"]
    assert result["modo"] == "lotes" and result["lotes"] == 3 and result["total"] == 2
    assert "UMA PARTE das fontes" in container.llm.prompts_for("EXTRACAO_HISTORIAS")[0]

    analysis = analysis_detail(container, result["analise_id"])
    [user] = items_of(analysis, "usuario")
    assert len(user["evidencias"]) == 3  # mesmo usuário em 3 lotes: um item, evidências unidas
    goals = {i["codigo"]: i["texto"] for i in items_of(analysis, "requisito_negocio")}
    assert goals == {"RN1": "acompanhar o desempenho", "RN2": "manter o cadastro atualizado"}
    needs = {i["codigo"]: (i["texto"], i["referencias"], len(i["historia_ids"])) for i in items_of(analysis, "funcionalidade")}
    assert needs == {"FN1": ("emitir o boletim", ["RN1"], 1), "FN2": ("cadastrar novos alunos", ["RN2"], 1)}


# ---------- análise revisada como contexto ----------

def test_reviewed_analysis_guides_next_generation(container, project_id, audio_id, document_id):
    container.llm.handlers["EXTRACAO_HISTORIAS"] = full_response
    first = generate(container, project_id, [audio_id])["resultado"]
    assert first["analise_revisada_usada"] is False
    assert "ANÁLISE REVISADA" not in container.llm.prompts_for("EXTRACAO_HISTORIAS")[0]

    with container.uow() as uow:
        AnalysisService(uow).update(first["analise_id"], {"revisada": True}, "ana")
    second = generate(container, project_id, document_ids=[document_id])["resultado"]
    assert second["analise_revisada_usada"] is True and second["analise_id"] != first["analise_id"]
    reviewed = container.llm.prompts_for("EXTRACAO_HISTORIAS")[-1].split("ANÁLISE REVISADA PELO ANALISTA")[1]
    assert "Sistema: Sistema escolar de boletins" in reviewed
    assert "- coordenador pedagógico — acompanha o desempenho" in reviewed
    assert "- FN1: (coordenador pedagógico) emitir o boletim" in reviewed
    assert "Fora de escopo (não gere histórias para estes itens):\n- aprovar o cronograma — tarefa do projeto" in reviewed

    reviewed_analysis = analysis_detail(container, first["analise_id"])
    assert reviewed_analysis["revisada"] is True and reviewed_analysis["revisada_por"] == "ana"


# ---------- CRUD ----------

@pytest.fixture
def service(container):
    uow = container.uow()
    yield AnalysisService(uow)
    uow.commit()
    uow.close()


def test_manual_analysis_crud(service, project_id, audio_id):
    created = service.create(project_id, {"sistema": "Portal escolar", "itens": [
        {"tipo": "usuario", "texto": "secretaria", "complemento": "faz as matrículas"},
        {"tipo": "requisito_negocio", "texto": "reduzir erros de matrícula"},
        {"tipo": "funcionalidade", "texto": "cadastrar alunos", "complemento": "secretaria", "referencias": ["RN1"]},
    ]}, "ana")
    analysis_id = created["id"]
    assert created["origem"] == "manual" and created["revisada"] is False
    assert [(i["tipo"], i["codigo"]) for i in created["itens"]] == [
        ("usuario", None), ("requisito_negocio", "RN1"), ("funcionalidade", "FN1")]

    chunk_id = service.uow.chunks.list_by_source("audio", audio_id)[0]["id"]
    extra = service.add_item(analysis_id, {"tipo": "requisito_negocio", "texto": "agilizar o atendimento",
                                           "trecho_ids": [chunk_id]})
    assert extra["codigo"] == "RN2" and extra["origem"] == "manual"
    assert extra["evidencias"][0]["trecho_id"] == chunk_id and "boletim" in extra["evidencias"][0]["texto"]

    functionality = items_of(service.detail(analysis_id), "funcionalidade")[0]
    updated = service.update_item(analysis_id, functionality["id"], {"texto": "matricular alunos", "referencias": ["RN1", "RN2"]})
    assert updated["texto"] == "matricular alunos" and updated["editado"] is True and updated["referencias"] == ["RN1", "RN2"]

    rn1 = items_of(service.detail(analysis_id), "requisito_negocio")[0]
    service.delete_item(analysis_id, rn1["id"])
    assert items_of(service.detail(analysis_id), "funcionalidade")[0]["referencias"] == ["RN2"]  # referência removida

    reviewed = service.update(analysis_id, {"sistema": "Portal da escola", "revisada": True}, "bia")
    assert reviewed["sistema"] == "Portal da escola" and reviewed["revisada_por"] == "bia"
    assert [a["id"] for a in service.list(project_id)["itens"]] == [analysis_id]

    service.delete(analysis_id)
    with pytest.raises(NotFoundError):
        service.detail(analysis_id)


def test_item_validation(service, project_id):
    analysis_id = service.create(project_id, {"sistema": None}, "ana")["id"]
    with pytest.raises(BusinessValidationError, match="Requisitos de negócio não encontrados"):
        service.add_item(analysis_id, {"tipo": "funcionalidade", "texto": "x", "referencias": ["RN9"]})
    with pytest.raises(BusinessValidationError, match="Somente funcionalidades"):
        service.add_item(analysis_id, {"tipo": "usuario", "texto": "x", "referencias": ["RN1"]})
    with pytest.raises(BusinessValidationError, match="Trechos não encontrados"):
        service.add_item(analysis_id, {"tipo": "usuario", "texto": "x", "trecho_ids": [99999]})
    other = service.create(project_id, {"itens": [{"tipo": "usuario", "texto": "y"}]}, "ana")
    with pytest.raises(NotFoundError):
        service.update_item(analysis_id, other["itens"][0]["id"], {"texto": "z"})  # item de outra análise
    with pytest.raises(NotFoundError):
        service.list(99999)
