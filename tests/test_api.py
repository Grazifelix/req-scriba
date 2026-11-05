"""Testes de integração da API (requerem fastapi + httpx instalados)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from tests.conftest import DOC_TEXT, WAV_BYTES  # noqa: E402

API = "/api/v1"
ALLOWED_ORIGIN = "http://localhost:5173"


@pytest.fixture
def client(container):
    container.settings.cors_origins = [ALLOWED_ORIGIN]  # independe de um .env local
    with TestClient(create_app(container=container), raise_server_exceptions=False) as test_client:
        yield test_client


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["erro"]["codigo"] == code and body["erro"]["mensagem"]


def create_project(client):
    response = client.post(f"{API}/projetos", json={"titulo": "Escola", "descricao": "Teste"})
    assert response.status_code == 201
    return response.json()["id"]


def test_health_and_root(client):
    health = client.get(f"{API}/health").json()
    assert health["status"] == "ok" and set(health["componentes"]) == {
        "banco_relacional", "banco_vetorial", "llm", "embeddings", "transcricao"}
    assert health["componentes"]["llm"]["provedor"] == "fake"
    assert health["componentes"]["embeddings"]["provedor"] == "hash"
    assert client.get("/").json()["documentacao"] == "/docs"


def test_health_flags_pending_reindex_when_vectors_are_missing(client, container, document_id):
    assert client.get(f"{API}/health").json()["componentes"]["banco_vetorial"]["reindexacao_pendente"] is False
    container.vector_store.items.clear()  # equivale a trocar o modelo: coleção nova, ainda vazia
    health = client.get(f"{API}/health").json()
    assert health["status"] == "degradado"
    assert health["componentes"]["banco_vetorial"]["status"] == "reindexacao_pendente"


def test_preflight_allows_configured_origin_and_user_header(client):
    response = client.options(f"{API}/projetos", headers={
        "Origin": ALLOWED_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-usuario,content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "x-usuario" in response.headers["access-control-allow-headers"].lower()


def test_unlisted_origin_receives_no_cors_header(client):
    origin = {"Origin": "http://origem-nao-autorizada.example"}
    assert "access-control-allow-origin" not in client.get(f"{API}/health", headers=origin).headers
    preflight = client.options(f"{API}/projetos", headers={**origin, "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 400 and "access-control-allow-origin" not in preflight.headers


def test_business_error_keeps_cors_and_json_format(client):
    response = client.get(f"{API}/projetos/999999", headers={"Origin": ALLOWED_ORIGIN})
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.status_code == 404 and response.json()["erro"]["mensagem"]


def test_unexpected_error_reaches_browser_with_cors_and_generic_json(client):
    def explode() -> None:
        raise RuntimeError("detalhe interno sensivel")

    client.app.add_api_route("/falha-inesperada", explode)
    response = client.get("/falha-inesperada", headers={"Origin": ALLOWED_ORIGIN})
    assert_error(response, 500, "erro_interno")
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sensivel" not in response.text


def test_full_flow(client):
    headers = {"X-Usuario": "ana"}
    project_id = create_project(client)

    # US-01/02: upload → 202 → transcrição em background
    response = client.post(f"{API}/projetos/{project_id}/audios", files={"arquivo": ("reuniao.wav", WAV_BYTES, "audio/wav")})
    assert response.status_code == 202 and response.json()["status"] == "pendente"
    audio_id = response.json()["id"]
    assert client.get(f"{API}/audios/{audio_id}").json()["status"] == "concluido"
    transcript = client.get(f"{API}/audios/{audio_id}/transcricao").json()
    assert transcript["total_segmentos"] == 3
    assert client.get(f"{API}/audios/{audio_id}/transcricao/busca", params={"termo": "boletim"}).json()["total"] == 1
    segment_id = transcript["segmentos"][0]["id"]
    patched = client.patch(f"{API}/audios/{audio_id}/segmentos/{segment_id}",
                           json={"texto": "Precisamos emitir o boletim da turma."})
    assert patched.status_code == 200 and patched.json()["editado"]
    audio_file = client.get(f"{API}/audios/{audio_id}/arquivo")
    assert audio_file.status_code == 200 and audio_file.headers["content-type"].startswith("audio/wav")

    # US-04
    response = client.post(f"{API}/projetos/{project_id}/documentos",
                           files={"arquivo": ("escopo.txt", DOC_TEXT.encode(), "text/plain")})
    assert response.status_code == 202
    document_id = response.json()["id"]
    assert client.get(f"{API}/documentos/{document_id}").json()["status"] == "concluido"
    assert client.get(f"{API}/documentos/{document_id}/conteudo").json()["total_trechos"] >= 1
    assert client.get(f"{API}/projetos/{project_id}/busca", params={"termo": "boletim"}).json()["total"] >= 1

    # US-05: geração (job)
    job = client.post(f"{API}/projetos/{project_id}/historias/gerar",
                      json={"audio_ids": [audio_id], "documento_ids": [document_id]}, headers=headers)
    assert job.status_code == 202
    job = client.get(f"{API}/jobs/{job.json()['id']}").json()
    assert job["status"] == "concluido" and job["resultado"]["total"] == 2
    story_id = job["resultado"]["historias_criadas"][0]
    story = client.get(f"{API}/historias/{story_id}").json()
    assert story["validada"] is False and story["fontes_audio"]
    assert client.get(f"{API}/projetos/{project_id}/historias", params={"status": "em_revisao"}).json()["total"] == 1

    # US-07/08/09
    evaluation = client.post(f"{API}/historias/{story_id}/avaliar")
    assert evaluation.status_code == 200 and evaluation.json()["qualidade_geral"]["total_criterios"] == 13
    edited = client.patch(f"{API}/historias/{story_id}", json={"titulo": "Boletim bimestral"}, headers=headers)
    assert edited.json()["versao_atual"] == 2 and edited.json()["versoes"][1]["responsavel"] == "ana"
    client.post(f"{API}/historias/{story_id}/avaliar")
    assert client.get(f"{API}/historias/{story_id}/avaliacoes/comparacao").status_code == 200
    assert client.get(f"{API}/historias/{story_id}/defeitos").json()["total"] >= 1
    assert client.get(f"{API}/projetos/{project_id}/qualidade").json()["total"] == 2

    # US-10/11/12
    generated = client.post(f"{API}/historias/{story_id}/perguntas/gerar").json()
    question_id = generated["perguntas"][0]["id"]
    assert client.patch(f"{API}/perguntas/{question_id}", json={"pergunta": "Qual formato?"}).json()["editada"]
    assert client.post(f"{API}/perguntas/{question_id}/aprovar").json()["status"] == "aprovada"
    followup = client.get(f"{API}/projetos/{project_id}/followup", params={"agrupar_por": "tipo_defeito"}).json()
    assert followup["total_perguntas"] == 1
    answered = client.post(f"{API}/perguntas/{question_id}/responder", json={"resposta": "PDF"})
    assert answered.json()["status"] == "respondida"

    # US-06
    assert client.post(f"{API}/historias/{story_id}/aprovar").json()["status"] == "aprovada"

    # US-13/14/15
    new_doc = client.post(f"{API}/projetos/{project_id}/documentos",
                          files={"arquivo": ("ata2.txt", "O boletim será em PDF.".encode(), "text/plain")}).json()["id"]
    analysis = client.post(f"{API}/projetos/{project_id}/atualizacoes/analisar", json={"documento_ids": [new_doc]})
    assert client.get(f"{API}/jobs/{analysis.json()['id']}").json()["status"] == "concluido"
    suggestions = client.get(f"{API}/projetos/{project_id}/sugestoes-atualizacao", params={"status": "pendente"}).json()
    suggestion_id = next(s["id"] for s in suggestions["itens"] if s["historia_id"] == story_id)
    assert client.get(f"{API}/historias/{story_id}").json()["versao_atual"] == 2  # intocada (US-14 CA04)
    detail = client.get(f"{API}/sugestoes-atualizacao/{suggestion_id}").json()
    assert detail["comparacao"]["campos_alterados"]
    approved = client.post(f"{API}/sugestoes-atualizacao/{suggestion_id}/aprovar",
                           json={"conteudo_proposto": {"beneficio": "acompanhar o bimestre"}}, headers=headers)
    assert approved.json()["status"] == "aprovada" and approved.json()["versao_aplicada"] == 3
    assert_error(client.post(f"{API}/sugestoes-atualizacao/{suggestion_id}/rejeitar"), 409, "conflito")

    # US-18/19/20
    versions = client.get(f"{API}/historias/{story_id}/versoes").json()
    assert [v["numero"] for v in versions["itens"]] == [1, 2, 3]
    assert versions["itens"][2]["origem_alteracao"] == "sugestao_ia_editada"
    assert client.get(f"{API}/historias/{story_id}/versoes/1").json()["numero"] == 1
    trace = client.get(f"{API}/historias/{story_id}/rastreabilidade").json()
    assert trace["versoes"][2]["novas_evidencia_ids"]
    assert client.get(f"{API}/historias/{story_id}/evidencias", params={"versao": 1}).json()["total"] >= 1

    # Projeto: totais e exclusão
    project = client.get(f"{API}/projetos/{project_id}").json()
    assert project["total_historias_prontas"] == 1 and story_id in project["backlog_aprovado"]
    assert client.delete(f"{API}/projetos/{project_id}").status_code == 204
    assert_error(client.get(f"{API}/projetos/{project_id}"), 404, "nao_encontrado")


def test_error_contract(client):
    project_id = create_project(client)
    assert_error(client.get(f"{API}/historias/999"), 404, "nao_encontrado")
    assert_error(client.get(f"{API}/rota-inexistente"), 404, "http_404")
    assert_error(client.post(f"{API}/projetos", json={"titulo": ""}), 422, "entrada_invalida")
    assert_error(client.post(f"{API}/projetos", json={"titulo": "x", "extra": 1}), 422, "entrada_invalida")
    bad_upload = client.post(f"{API}/projetos/{project_id}/audios", files={"arquivo": ("a.exe", b"MZ", "application/x")})
    assert_error(bad_upload, 422, "validacao")
    assert ".mp3" in bad_upload.json()["erro"]["detalhes"]["extensoes_aceitas"]
    assert_error(client.post(f"{API}/projetos/{project_id}/historias/gerar", json={}), 422, "validacao")
    story = client.post(f"{API}/projetos/{project_id}/historias", json={"funcionalidade": "lançar notas"}).json()
    assert_error(client.get(f"{API}/historias/{story['id']}/avaliacoes/comparacao"), 409, "conflito")


def test_external_failure_returns_502(client, container):
    project_id = create_project(client)
    story = client.post(f"{API}/projetos/{project_id}/historias", json={"funcionalidade": "lançar notas"}).json()
    container.llm.fail = True
    assert_error(client.post(f"{API}/historias/{story['id']}/avaliar"), 502, "servico_externo")


def test_lists_and_security_headers(client):
    project_id = create_project(client)
    for path in ("/projetos", f"/projetos/{project_id}/audios", f"/projetos/{project_id}/documentos",
                 f"/projetos/{project_id}/historias", f"/projetos/{project_id}/perguntas",
                 f"/projetos/{project_id}/sugestoes-atualizacao", f"/projetos/{project_id}/jobs"):
        response = client.get(API + path)
        assert set(response.json()) >= {"itens", "total"}, path
    response = client.get(f"{API}/projetos")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_openapi_documents_all_routes(client):
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    expected = {
        "/health", "/projetos", "/projetos/{projeto_id}", "/projetos/{projeto_id}/audios", "/audios/{audio_id}",
        "/audios/{audio_id}/arquivo", "/audios/{audio_id}/reprocessar", "/audios/{audio_id}/transcricao",
        "/audios/{audio_id}/transcricao/busca", "/audios/{audio_id}/segmentos/{segmento_id}",
        "/projetos/{projeto_id}/documentos", "/documentos/{documento_id}", "/documentos/{documento_id}/conteudo",
        "/documentos/{documento_id}/reprocessar", "/projetos/{projeto_id}/busca",
        "/projetos/{projeto_id}/historias/gerar", "/projetos/{projeto_id}/historias", "/historias/{historia_id}",
        "/historias/{historia_id}/aprovar", "/historias/{historia_id}/desaprovar",
        "/historias/{historia_id}/evidencias", "/historias/{historia_id}/rastreabilidade",
        "/historias/{historia_id}/versoes", "/historias/{historia_id}/versoes/{numero}",
        "/historias/{historia_id}/avaliar", "/historias/{historia_id}/avaliacoes",
        "/historias/{historia_id}/avaliacoes/comparacao", "/historias/{historia_id}/defeitos",
        "/projetos/{projeto_id}/qualidade", "/historias/{historia_id}/perguntas/gerar",
        "/projetos/{projeto_id}/perguntas", "/projetos/{projeto_id}/followup", "/perguntas/{pergunta_id}",
        "/perguntas/{pergunta_id}/aprovar", "/perguntas/{pergunta_id}/rejeitar", "/perguntas/{pergunta_id}/responder",
        "/projetos/{projeto_id}/atualizacoes/analisar", "/projetos/{projeto_id}/sugestoes-atualizacao",
        "/sugestoes-atualizacao/{sugestao_id}", "/sugestoes-atualizacao/{sugestao_id}/aprovar",
        "/sugestoes-atualizacao/{sugestao_id}/rejeitar", "/jobs/{job_id}", "/projetos/{projeto_id}/jobs",
    }
    assert {API + p for p in expected} <= paths
    for path, operations in spec["paths"].items():
        for operation in operations.values():
            assert operation.get("summary"), f"{path} sem summary"
