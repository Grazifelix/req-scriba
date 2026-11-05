import io
from pathlib import Path

import pytest
import requests

from app.core.config import Settings
from app.core.container import build_embedder, build_llm, embedding_fingerprint
from app.core.errors import BusinessValidationError, ExternalServiceError
from app.integrations.embeddings import HashEmbedder
from app.integrations.extractors import extract_pages
from app.integrations.ollama import OllamaClient
from app.integrations.openai_compatible import OpenAICompatibleClient
from app.integrations.storage import AUDIO_EXTENSIONS, DOCUMENT_EXTENSIONS, FileStorage, sanitize_filename
from app.integrations.vector_store import InMemoryVectorStore, build_where, collection_name


class FakeResponse:
    def __init__(self, payload=None, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, timeout):
        return self.responses.pop(0)


def client(responses):
    return OllamaClient("http://ollama:11434", "qwen3.5:4b", "bge-m3", session=FakeSession(responses))


# -- Ollama -----------------------------------------------------------------
def test_generate_json_success_uses_json_format():
    c = client([FakeResponse({"message": {"content": '{"ok": true}'}})])
    assert c.generate_json("sys", "prompt") == {"ok": True}
    url, payload = c.session.posts[0]
    assert url.endswith("/api/chat") and payload["format"] == "json" and payload["stream"] is False
    assert payload["model"] == "qwen3.5:4b" and payload["options"]["num_ctx"] == 8192
    assert payload["think"] is False


def test_generate_json_retries_once_on_invalid_json():
    c = client([FakeResponse({"message": {"content": "isto não é json"}}),
                FakeResponse({"message": {"content": '{"ok": 1}'}})])
    assert c.generate_json("sys", "prompt") == {"ok": 1}
    assert len(c.session.posts) == 2
    assert "JSON válido" in c.session.posts[1][1]["messages"][-1]["content"]


def test_generate_json_fails_after_retry():
    c = client([FakeResponse({"message": {"content": "x"}}), FakeResponse({"message": {"content": "y"}})])
    with pytest.raises(ExternalServiceError):
        c.generate_json("sys", "prompt")


def test_timeout_and_connection_errors_become_external_errors():
    with pytest.raises(ExternalServiceError, match="Tempo esgotado"):
        client([requests.Timeout()]).generate_json("s", "p")
    with pytest.raises(ExternalServiceError, match="Não foi possível conectar"):
        client([requests.ConnectionError()]).generate_json("s", "p")
    with pytest.raises(ExternalServiceError, match="HTTP 500"):
        client([FakeResponse({}, status=500, text="boom")]).generate_json("s", "p")


def test_embed_validates_response():
    c = client([FakeResponse({"embeddings": [[0.1, 0.2]]})])
    assert c.embed(["a"]) == [[0.1, 0.2]]
    with pytest.raises(ExternalServiceError):
        client([FakeResponse({"embeddings": []})]).embed(["a"])


def test_health_checks_llm_and_embedding_models_separately():
    tags = {"models": [{"name": "qwen3.5:4b"}]}
    health = client([FakeResponse(tags)]).health()
    assert health["status"] == "ok" and health["provedor"] == "ollama" and health["modelo"] == "qwen3.5:4b"
    embedding_health = client([FakeResponse(tags)]).embedding_health()
    assert embedding_health["status"] == "modelos_ausentes" and embedding_health["modelos_ausentes"] == ["bge-m3"]


# -- Provedor compatível com OpenAI -----------------------------------------
API_KEY = "chave-secreta-123"


def compatible_client(responses, api_key=API_KEY):
    session = FakeSession(responses)
    session.headers = {}
    return OpenAICompatibleClient("http://llm.local/v1/", "modelo-chat", "modelo-vetor", api_key=api_key, session=session)


def chat_response(content):
    return FakeResponse({"choices": [{"message": {"content": content}}]})


def test_compatible_generate_json_uses_json_mode_and_bearer_key():
    c = compatible_client([chat_response('{"ok": true}')])
    assert c.generate_json("sys", "prompt") == {"ok": True}
    url, payload = c.session.posts[0]
    assert url == "http://llm.local/v1/chat/completions"
    assert payload["model"] == "modelo-chat" and payload["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert c.session.headers["Authorization"] == f"Bearer {API_KEY}"


def test_compatible_client_sends_no_authorization_without_key():
    assert "Authorization" not in compatible_client([], api_key="").session.headers


def test_compatible_generate_json_retries_once_on_invalid_json():
    c = compatible_client([chat_response("isto não é json"), chat_response('{"ok": 1}')])
    assert c.generate_json("sys", "prompt") == {"ok": 1}
    assert "JSON válido" in c.session.posts[1][1]["messages"][-1]["content"]


@pytest.mark.parametrize("failure, message", [
    (requests.Timeout(), "Tempo esgotado"),
    (requests.ConnectionError(), "Não foi possível conectar"),
    (FakeResponse({}, status=401, text="sem acesso"), "HTTP 401"),
    (FakeResponse({}, status=429, text="limite"), "HTTP 429"),
    (FakeResponse({}, status=500, text="boom"), "HTTP 500"),
    (FakeResponse({"choices": []}), "Resposta de chat inválida"),
])
def test_compatible_failures_become_external_errors(failure, message):
    with pytest.raises(ExternalServiceError, match=message):
        compatible_client([failure]).generate_json("s", "p")


def test_compatible_errors_never_expose_api_key():
    failure = FakeResponse({}, status=401, text=f"chave inválida: {API_KEY}")
    with pytest.raises(ExternalServiceError) as exc:
        compatible_client([failure]).generate_json("s", "p")
    assert API_KEY not in str(exc.value.to_dict()) and "***" in exc.value.details["resposta"]


def test_compatible_embed_restores_input_order_and_validates():
    items = [{"index": 1, "embedding": [0.2]}, {"index": 0, "embedding": [0.1]}]
    c = compatible_client([FakeResponse({"data": items})])
    assert c.embed(["a", "b"]) == [[0.1], [0.2]]
    assert c.session.posts[0][0] == "http://llm.local/v1/embeddings"
    for invalid in ({"data": []}, {"erro": "x"}, {"data": [{"index": 0}]}):
        with pytest.raises(ExternalServiceError):
            compatible_client([FakeResponse(invalid)]).embed(["a"])


def test_compatible_health_reports_models_and_unavailability():
    models = {"data": [{"id": "modelo-chat"}]}
    assert compatible_client([FakeResponse(models)]).health()["status"] == "ok"
    assert compatible_client([FakeResponse(models)]).embedding_health()["modelos_ausentes"] == ["modelo-vetor"]
    assert compatible_client([FakeResponse({}, status=401)]).health()["status"] == "indisponivel"


# -- Seleção de provedores --------------------------------------------------
def settings_for(**values):
    defaults = {
        "llm_provider": "ollama", "llm_base_url": "", "llm_api_key": "", "embeddings_provider": "ollama",
        "embeddings_base_url": "", "embeddings_api_key": "", "ollama_url": "http://localhost:11434",
    }
    return Settings(**{**defaults, **values})


def test_default_provider_is_ollama_at_configured_url():
    settings = settings_for(ollama_url="http://localhost:9999")
    assert isinstance(build_llm(settings), OllamaClient) and build_llm(settings).base_url == "http://localhost:9999"
    assert isinstance(build_embedder(settings).client, OllamaClient)


def test_providers_are_selected_independently():
    settings = settings_for(llm_provider="openai_compatible", llm_base_url="http://localhost:1234/v1")
    assert isinstance(build_llm(settings), OpenAICompatibleClient)
    assert isinstance(build_embedder(settings).client, OllamaClient)
    assert isinstance(build_embedder(settings_for(embeddings_provider="hash")), HashEmbedder)


def test_embedder_inherits_llm_connection_only_for_same_provider_without_own_url():
    shared = {"llm_provider": "openai_compatible", "embeddings_provider": "openai_compatible",
              "llm_base_url": "http://localhost:1234/v1", "llm_api_key": "chave-llm"}
    inherited = build_embedder(settings_for(**shared)).client
    assert inherited.base_url == "http://localhost:1234/v1" and inherited.api_key == "chave-llm"
    own = build_embedder(settings_for(**shared, embeddings_base_url="http://localhost:5678/v1")).client
    assert own.base_url == "http://localhost:5678/v1" and own.api_key == ""  # chave nunca vai a outro servidor


@pytest.mark.parametrize("values, variable", [
    ({"llm_provider": "ollamaa"}, "REQSCRIBA_LLM_PROVIDER"),
    ({"llm_provider": "openai_compatible"}, "REQSCRIBA_LLM_BASE_URL"),
])
def test_invalid_llm_configuration_fails_with_clear_message(values, variable):
    with pytest.raises(ValueError, match=variable):
        build_llm(settings_for(**values))


@pytest.mark.parametrize("values, variable", [
    ({"embeddings_provider": "ollamaa"}, "REQSCRIBA_EMBEDDINGS_PROVIDER"),
    ({"embeddings_provider": "openai_compatible"}, "REQSCRIBA_EMBEDDINGS_BASE_URL"),
])
def test_invalid_embeddings_configuration_fails_with_clear_message(values, variable):
    with pytest.raises(ValueError, match=variable):
        build_embedder(settings_for(**values))


def test_remote_provider_logs_warning_and_local_does_not(caplog):
    with caplog.at_level("WARNING"):
        build_llm(settings_for())
        assert "fora desta máquina" not in caplog.text
        build_llm(settings_for(llm_provider="openai_compatible", llm_base_url="https://llm.example/v1", llm_api_key=API_KEY))
    assert "llm.example" in caplog.text and API_KEY not in caplog.text


def test_settings_repr_hides_api_keys():
    assert API_KEY not in repr(settings_for(llm_api_key=API_KEY, embeddings_api_key=API_KEY))


def test_embedding_fingerprint_separates_models():
    assert embedding_fingerprint(settings_for(embedding_model="a")) != embedding_fingerprint(settings_for(embedding_model="b"))
    assert embedding_fingerprint(settings_for(embeddings_provider="hash", embedding_model="a")) == "hash"


# -- Armazenamento ----------------------------------------------------------
def test_sanitize_filename_blocks_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("relatório final (v2).pdf") == "relat_rio_final_v2_.pdf"
    assert sanitize_filename("") == "arquivo"


def test_storage_rejects_unsupported_extension(tmp_path: Path):
    storage = FileStorage(tmp_path)
    with pytest.raises(BusinessValidationError, match="não suportado") as exc:
        storage.save(1, "virus.exe", io.BytesIO(b"MZ"), AUDIO_EXTENSIONS, 1000)
    assert ".mp3" in exc.value.details["extensoes_aceitas"]


def test_storage_rejects_empty_mismatched_and_large(tmp_path: Path):
    storage = FileStorage(tmp_path)
    with pytest.raises(BusinessValidationError, match="vazio"):
        storage.save(1, "a.wav", io.BytesIO(b""), AUDIO_EXTENSIONS, 1000)
    with pytest.raises(BusinessValidationError, match="não corresponde"):
        storage.save(1, "a.pdf", io.BytesIO(b"texto"), DOCUMENT_EXTENSIONS, 1000)
    with pytest.raises(BusinessValidationError, match="tamanho máximo"):
        storage.save(1, "a.txt", io.BytesIO(b"a" * 5000), DOCUMENT_EXTENSIONS, 1000)
    assert not any((tmp_path / "projeto_1").iterdir())  # arquivo parcial removido


def test_storage_saves_and_resolves_inside_root(tmp_path: Path):
    storage = FileStorage(tmp_path)
    stored = storage.save(3, "notas.txt", io.BytesIO(b"conteudo"), DOCUMENT_EXTENSIONS, 1000)
    assert stored.size_bytes == 8 and stored.path.parent.name == "projeto_3"
    assert storage.resolve(str(stored.path)) == stored.path.resolve()
    with pytest.raises(BusinessValidationError):
        storage.resolve("/etc/passwd")


# -- Extratores -------------------------------------------------------------
def test_extract_txt(tmp_path: Path):
    path = tmp_path / "a.txt"
    path.write_text("Olá, mundo", encoding="utf-8")
    [page] = extract_pages(path)
    assert page.number is None and page.text == "Olá, mundo"


def test_extract_docx_with_table(tmp_path: Path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Requisito: emitir boletim.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Campo", "Nota"
    path = tmp_path / "a.docx"
    document.save(path)
    [page] = extract_pages(path)
    assert "emitir boletim" in page.text and "Campo | Nota" in page.text


def test_extract_pdf_without_text_is_rejected(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "scan.pdf"
    with path.open("wb") as fh:
        writer.write(fh)
    with pytest.raises(BusinessValidationError, match="não possui texto extraível"):
        extract_pages(path)


def test_extract_corrupted_docx(tmp_path: Path):
    path = tmp_path / "x.docx"
    path.write_bytes(b"PK\x03\x04 corrompido")
    with pytest.raises(BusinessValidationError, match="DOCX inválido"):
        extract_pages(path)


# -- Vetores ----------------------------------------------------------------
def test_build_where_formats():
    assert build_where(1) == {"projeto_id": 1}
    assert build_where(1, ["audio:2"]) == {"$and": [{"projeto_id": 1}, {"fonte_chave": {"$in": ["audio:2"]}}]}


def test_in_memory_store_filters_and_ranks():
    store = InMemoryVectorStore()
    store.upsert(["a", "b", "c"], ["x", "y", "z"], [[1, 0], [0, 1], [1, 0.1]], [
        {"projeto_id": 1, "fonte_chave": "audio:1"}, {"projeto_id": 1, "fonte_chave": "audio:2"},
        {"projeto_id": 2, "fonte_chave": "audio:3"},
    ])
    assert [h["id"] for h in store.query([1, 0], 5, build_where(1))] == ["a", "b"]
    assert [h["id"] for h in store.query([1, 0], 5, build_where(1, ["audio:2"]))] == ["b"]
    store.delete_where(build_where(1))
    assert list(store.items) == ["c"]


def test_chroma_store_roundtrip(tmp_path: Path):
    pytest.importorskip("chromadb")
    from app.integrations.vector_store import ChromaVectorStore

    store = ChromaVectorStore(tmp_path / "chroma")
    store.upsert(["trecho-1", "trecho-2"], ["boletim", "matrícula"], [[1.0, 0.0], [0.0, 1.0]], [
        {"projeto_id": 1, "fonte_chave": "documento:1"}, {"projeto_id": 1, "fonte_chave": "documento:2"},
    ])
    hits = store.query([1.0, 0.0], 1, build_where(1, ["documento:1", "documento:2"]))
    assert hits[0]["id"] == "trecho-1"
    reopened = ChromaVectorStore(tmp_path / "chroma")  # persistência em disco
    assert reopened.health()["vetores"] == 2


def test_chroma_keeps_embedding_models_in_separate_collections(tmp_path: Path):
    pytest.importorskip("chromadb")
    from app.integrations.vector_store import ChromaVectorStore

    first = ChromaVectorStore(tmp_path / "chroma", collection_name("modelo-a"))
    first.upsert(["trecho-1"], ["boletim"], [[1.0, 0.0]], [{"projeto_id": 1}])
    second = ChromaVectorStore(tmp_path / "chroma", collection_name("modelo-b:latest"))
    assert second.health()["vetores"] == 0
    second.upsert(["trecho-1"], ["boletim"], [[1.0, 0.0, 0.0]], [{"projeto_id": 1}])  # outra dimensão
    assert first.health()["vetores"] == 1 and second.health()["vetores"] == 1
