import io
from pathlib import Path

import pytest
import requests

from app.core.errors import BusinessValidationError, ExternalServiceError
from app.integrations.extractors import extract_pages
from app.integrations.ollama import OllamaClient
from app.integrations.storage import AUDIO_EXTENSIONS, DOCUMENT_EXTENSIONS, FileStorage, sanitize_filename
from app.integrations.vector_store import InMemoryVectorStore, build_where


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


def test_health_reports_missing_models():
    c = client([FakeResponse({"models": [{"name": "qwen3.5:4b"}]})])
    health = c.health()
    assert health["status"] == "modelos_ausentes" and health["modelos_ausentes"] == ["bge-m3"]


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
