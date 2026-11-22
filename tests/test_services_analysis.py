"""Análise das fontes: job próprio, análise gravada e rastreável, atualização com novas fontes, aprovação
humana como pré-requisito da geração e uso da análise aprovada no prompt de histórias."""

import pytest

from app.core.errors import BusinessValidationError, NotFoundError
from app.services.analysis import AnalysisService
from app.services.generation import run_generation, start_generation
from app.services.source_analysis import run_source_analysis, start_source_analysis
from app.services.stories import StoryService
from tests.conftest import ensure_approved_analysis


def analyze(container, project_id, audio_ids=(), document_ids=()):
    with container.uow() as uow:
        job = start_source_analysis(uow, project_id, list(audio_ids), list(document_ids), "ana")
    assert job["tipo"] == "analise_fontes"
    run_source_analysis(container, job["id"])
    with container.uow() as uow:
        return uow.jobs.get(job["id"])


def approve(container, analysis_id):
    with container.uow() as uow:
        return AnalysisService(uow).update(analysis_id, {"revisada": True}, "ana")


def generate(container, project_id, audio_ids=(), document_ids=(), analysis_id=None):
    with container.uow() as uow:
        job = start_generation(uow, project_id, list(audio_ids), list(document_ids), "ana", analysis_id)
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


def analysis_response(prompt):
    """F1 = transcrição, F2 = documento; FN2 e F9 não são rótulos válidos."""
    return {"analise": {
        "sistema": "Sistema escolar de boletins",
        "participantes": [{"nome": "Ana", "papel_na_reuniao": "diretora", "fontes": ["F1"]}],
        "usuarios": [{"papel": "coordenador pedagógico", "descricao": "acompanha o desempenho", "fontes": ["F1"]}],
        "requisitos_negocio": [{"id": "RN1", "descricao": "acompanhar o desempenho dos alunos", "fontes": ["F1"]}],
        "funcionalidades": [{"id": "FN1", "usuario": "coordenador pedagógico", "descricao": "emitir o boletim",
                             "requisitos_negocio": ["RN1"], "fontes": ["F1", "FN2"]}],
        "fora_de_escopo": [{"descricao": "aprovar o cronograma", "motivo": "tarefa do projeto", "fontes": ["F9"]}],
    }}


def stories_response(prompt):
    return {
        "historias": [
            {"id": "H1", "funcionalidade_ref": "FN1", "titulo": "Emitir boletim", "perfil": "coordenador pedagógico",
             "funcionalidade": "Emitir o boletim da turma", "beneficio": "acompanhar o desempenho dos alunos",
             "fontes": ["F1", "FN2"], "citacao": "Precisamos emitir o boletim da turma todo bimestre"},
            {"id": "H2", "funcionalidade_ref": "FN7", "titulo": "Publicar boletim", "perfil": "coordenador pedagógico",
             "funcionalidade": "publicar o boletim no portal", "fontes": ["F1"],
             "citacao": "o boletim será publicado no portal"},
        ],
        "lacunas_conjunto": [{"tipo": "conflito", "historias": ["H1", "H2"], "descricao": "Canal de entrega divergente."}],
    }


# ---------- job de análise ----------

def test_analysis_job_saves_traceable_analysis_without_stories(container, project_id, audio_id, document_id):
    container.llm.handlers["ANALISE_FONTES"] = analysis_response
    job = analyze(container, project_id, [audio_id], [document_id])
    result = job["resultado"]
    assert job["status"] == "concluido" and result["modo"] == "chamada_unica" and result["analise_anterior_id"] is None
    assert not container.llm.prompts_for("EXTRACAO_HISTORIAS")  # análise independente da geração
    [prompt] = container.llm.prompts_for("ANALISE_FONTES")
    assert "TODAS as fontes" in prompt and "NÃO escreva histórias" in prompt
    assert prompt.index("</fontes>") < prompt.index("### TAREFA") < prompt.index("ETAPA 1: Entender as fontes")
    assert '"historias"' not in prompt and "ANÁLISE ATUAL" not in prompt

    analysis = analysis_detail(container, result["analise_id"])
    assert analysis["sistema"] == "Sistema escolar de boletins" and analysis["origem"] == "ia"
    assert analysis["revisada"] is False and analysis["job_id"] == job["id"]
    assert {f["nome_arquivo"] for f in analysis["fontes"]} == {"reuniao.wav", "escopo.txt"}
    assert analysis["totais"] == {"participante": 1, "usuario": 1, "requisito_negocio": 1,
                                  "funcionalidade": 1, "fora_de_escopo": 1}
    [functionality] = items_of(analysis, "funcionalidade")
    assert functionality["codigo"] == "FN1" and functionality["referencias"] == ["RN1"]
    assert functionality["complemento"] == "coordenador pedagógico" and functionality["historia_ids"] == []
    [evidence] = functionality["evidencias"]  # "FN2" não vira a fonte F2
    assert evidence["tipo_fonte"] == "audio" and "boletim" in evidence["texto"]  # texto vem do banco
    [out_of_scope] = items_of(analysis, "fora_de_escopo")
    assert out_of_scope["complemento"] == "tarefa do projeto" and out_of_scope["possui_evidencia"] is False


def test_empty_analysis_response_saves_nothing(container, project_id, audio_id):
    container.llm.handlers["ANALISE_FONTES"] = lambda p: {"historias": []}
    result = analyze(container, project_id, [audio_id])["resultado"]
    assert result["analise_id"] is None and result["mensagem"]


def test_analysis_job_failure_marks_error(container, project_id, audio_id):
    container.llm.fail = True
    job = analyze(container, project_id, [audio_id])
    assert job["status"] == "erro" and "Todos os lotes falharam" in job["mensagem_erro"]


def test_new_sources_update_previous_analysis(container, project_id, audio_id, document_id):
    container.llm.handlers["ANALISE_FONTES"] = analysis_response
    first = analyze(container, project_id, [audio_id])["resultado"]["analise_id"]
    with container.uow() as uow:  # o analista corrige a funcionalidade e aprova
        service = AnalysisService(uow)
        fn1 = items_of(service.detail(first), "funcionalidade")[0]
        service.update_item(first, fn1["id"], {"texto": "emitir o boletim bimestral da turma"})
        service.update(first, {"revisada": True}, "ana")

    def updated(prompt):
        data = analysis_response(prompt)
        data["analise"]["funcionalidades"] = [
            {"id": "FN1", "descricao": "emitir o boletim bimestral", "fontes": ["F1"]},
            {"id": "FN2", "usuario": "secretaria", "descricao": "matricular alunos", "fontes": ["F1"]},
        ]
        return data

    container.llm.handlers["ANALISE_FONTES"] = updated
    result = analyze(container, project_id, document_ids=[document_id])["resultado"]
    assert result["analise_anterior_id"] == first and result["analise_id"] != first
    prompt = container.llm.prompts_for("ANALISE_FONTES")[-1]
    current = prompt.split("ANÁLISE ATUAL DO PROJETO")[1].split("### TAREFA")[0]
    assert "- FN1: (coordenador pedagógico) emitir o boletim bimestral da turma" in current
    assert prompt.index("</fontes>") < prompt.index("ANÁLISE ATUAL DO PROJETO") < prompt.index("### TAREFA")

    new = analysis_detail(container, result["analise_id"])
    assert new["revisada"] is False  # atualização precisa de nova aprovação
    assert {f["nome_arquivo"] for f in new["fontes"]} == {"reuniao.wav", "escopo.txt"}
    needs = {i["codigo"]: (i["texto"], i["editado"]) for i in items_of(new, "funcionalidade")}
    assert needs == {"FN1": ("emitir o boletim bimestral da turma", True), "FN2": ("matricular alunos", False)}
    fn1 = next(i for i in items_of(new, "funcionalidade") if i["codigo"] == "FN1")
    assert {e["tipo_fonte"] for e in fn1["evidencias"]} == {"audio", "documento"}  # evidências antigas + novas
    assert len(items_of(new, "usuario")) == 1  # o mesmo usuário não é duplicado
    assert analysis_detail(container, first)["revisada"] is True  # a anterior não muda


def test_batches_merge_analysis_and_renumber_codes(container, project_id):
    from tests.conftest import upload_audio

    container.settings.transcript_window_chars = 60
    container.settings.generation_batch_chars = 60
    audio_id = upload_audio(container, project_id)

    def per_batch(prompt):
        sources = prompt.split("</fontes>")[0]
        user = {"papel": "coordenador pedagógico", "fontes": ["F1"]}
        if "boletim" in sources:
            need, goal = "emitir o boletim", "acompanhar o desempenho"
        elif "cadastrar" in sources:
            need, goal = "cadastrar novos alunos", "manter o cadastro atualizado"
        else:
            return {"analise": {"usuarios": [user]}}
        return {"analise": {
            "usuarios": [user], "requisitos_negocio": [{"id": "RN1", "descricao": goal, "fontes": ["F1"]}],
            "funcionalidades": [{"id": "FN1", "descricao": need, "requisitos_negocio": ["RN1"], "fontes": ["F1"]}],
        }}

    container.llm.handlers["ANALISE_FONTES"] = per_batch
    result = analyze(container, project_id, [audio_id])["resultado"]
    assert result["modo"] == "lotes" and result["lotes"] == 3
    assert "UMA PARTE das fontes" in container.llm.prompts_for("ANALISE_FONTES")[0]

    analysis = analysis_detail(container, result["analise_id"])
    [user] = items_of(analysis, "usuario")
    assert len(user["evidencias"]) == 3  # mesmo usuário em 3 lotes: um item, evidências unidas
    goals = {i["codigo"]: i["texto"] for i in items_of(analysis, "requisito_negocio")}
    assert goals == {"RN1": "acompanhar o desempenho", "RN2": "manter o cadastro atualizado"}
    needs = {i["codigo"]: (i["texto"], i["referencias"]) for i in items_of(analysis, "funcionalidade")}
    assert needs == {"FN1": ("emitir o boletim", ["RN1"]), "FN2": ("cadastrar novos alunos", ["RN2"])}


# ---------- geração exige análise aprovada ----------

def test_generation_requires_approved_analysis(container, project_id, audio_id):
    with container.uow() as uow, pytest.raises(BusinessValidationError, match="aprove a análise"):
        start_generation(uow, project_id, [audio_id], [], "ana")
    analysis_id = analyze(container, project_id, [audio_id])["resultado"]["analise_id"]
    with container.uow() as uow, pytest.raises(BusinessValidationError, match="aprove a análise"):
        start_generation(uow, project_id, [audio_id], [], "ana")  # existe, mas não foi aprovada
    with container.uow() as uow, pytest.raises(BusinessValidationError, match="ainda não foi aprovada"):
        start_generation(uow, project_id, [audio_id], [], "ana", analysis_id)
    with container.uow() as uow, pytest.raises(NotFoundError):
        start_generation(uow, project_id, [audio_id], [], "ana", 99999)
    approve(container, analysis_id)
    with container.uow() as uow:
        job = start_generation(uow, project_id, [audio_id], [], "ana")
    assert job["parametros"]["analise_id"] == analysis_id


def test_generation_uses_approved_analysis_and_links_stories(container, project_id, audio_id, document_id):
    container.llm.handlers["ANALISE_FONTES"] = analysis_response
    container.llm.handlers["EXTRACAO_HISTORIAS"] = stories_response
    analysis_id = analyze(container, project_id, [audio_id], [document_id])["resultado"]["analise_id"]
    approve(container, analysis_id)
    result = generate(container, project_id, [audio_id], [document_id])["resultado"]
    assert result["analise_id"] == analysis_id and result["modo"] == "chamada_unica"
    assert not container.llm.prompts_for("CONTEXTO_REUNIAO") and not container.llm.prompts_for("CONSOLIDACAO_HISTORIAS")

    [prompt] = container.llm.prompts_for("EXTRACAO_HISTORIAS")
    assert prompt.index("</fontes>") < prompt.index("ANALISE DO PROJETO:") < prompt.index("### TAREFA")
    assert "ETAPA 1: Escrever as histórias" in prompt and "Entender as fontes" not in prompt
    assert '"analise"' not in prompt  # o formato de resposta não pede a análise
    project = prompt.split("ANALISE DO PROJETO:")[1].split("### TAREFA")[0]
    assert "Sistema: Sistema escolar de boletins" in project
    assert "- coordenador pedagógico — acompanha o desempenho" in project
    assert "- FN1: (coordenador pedagógico) emitir o boletim" in project
    assert "Fora de escopo (não gere histórias para estes itens):\n- aprovar o cronograma — tarefa do projeto" in project

    first, second = (detail(container, i) for i in result["historias_criadas"])
    assert first["funcionalidade"] == "emitir o boletim da turma"  # minúscula inicial
    assert first["fontes_audio"] and not first["fontes_documentos"]  # "FN2" não vira a fonte F2
    assert not any("Citação" in c for c in first["afirmacoes_sem_evidencia"])  # citação literal conferida
    assert any("Citação não encontrada" in c for c in second["afirmacoes_sem_evidencia"])
    for story in (first, second):
        assert any(g["elemento"] == "conflito" for g in story["informacoes_ausentes"])  # lacuna de conjunto

    [functionality] = items_of(analysis_detail(container, analysis_id), "funcionalidade")
    assert functionality["historia_ids"] == [first["id"]]  # FN7 não existe na análise: H2 não é ligada


def test_generation_with_explicit_analysis_id(container, project_id, audio_id):
    old = ensure_approved_analysis(container, project_id)
    new = analyze(container, project_id, [audio_id])["resultado"]["analise_id"]
    approve(container, new)
    assert generate(container, project_id, [audio_id], analysis_id=old)["resultado"]["analise_id"] == old
    assert generate(container, project_id, [audio_id])["resultado"]["analise_id"] == new  # aprovada mais recente


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
