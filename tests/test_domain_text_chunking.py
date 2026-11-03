import pytest

from app.domain import enums
from app.domain.chunking import split_text, window_segments
from app.domain.text import contains_normalized, normalize, similarity, unified_diff


def test_normalize_removes_accents_and_punctuation():
    assert normalize("  Ação,   Café!  ") == "acao cafe"


def test_similarity_detects_near_duplicates():
    a = "Como gestor, quero exportar o relatório mensal, para analisar custos."
    b = "Como gestor, quero exportar relatório mensal para analisar os custos"
    assert similarity(a, b) >= 0.9
    assert similarity(a, "Como aluno, quero ver minhas notas.") < 0.5
    assert similarity("", a) == 0.0


def test_contains_normalized_ignores_case_and_accents():
    assert contains_normalized("A Matrícula é feita na Secretaria", "matricula")
    assert not contains_normalized("texto", "")


def test_unified_diff_shows_changes():
    diff = unified_diff("quero emitir boletim", "quero emitir boletim em PDF")
    assert "+em" in diff and "+PDF" in diff


def test_split_text_offsets_match_original():
    text = ("Parágrafo um com várias frases. Outra frase aqui.\n\n" * 30).strip()
    chunks = split_text(text, chunk_size=200, overlap=40)
    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.start:chunk.end] == chunk.text
        assert len(chunk.text) <= 200


def test_split_text_has_overlap_and_covers_text():
    text = " ".join(f"palavra{i}" for i in range(300))
    chunks = split_text(text, chunk_size=150, overlap=30)
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start < prev.end  # há sobreposição
        assert text[nxt.start - 1] == " "  # começa no início de uma palavra


def test_split_text_validates_parameters():
    with pytest.raises(ValueError):
        split_text("abc", chunk_size=10, overlap=10)


def test_split_short_text_single_chunk():
    chunks = split_text("Texto curto.", 1000, 150)
    assert len(chunks) == 1 and chunks[0].text == "Texto curto."


def test_window_segments_respects_limit_and_times():
    segments = [{"id": i, "texto": "x" * 300, "inicio_segundos": i * 10.0, "fim_segundos": i * 10.0 + 9} for i in range(5)]
    windows = window_segments(segments, max_chars=650)
    assert [w.segment_ids for w in windows] == [[0, 1], [2, 3], [4]]
    assert windows[0].start_seconds == 0.0 and windows[0].end_seconds == 19.0
    assert all(len(w.text) <= 650 for w in windows)


def test_window_segments_skips_empty_text():
    segments = [{"id": 1, "texto": "  ", "inicio_segundos": 0, "fim_segundos": 1}]
    assert window_segments(segments) == []


def test_all_status_have_labels():
    """Acessibilidade: todo valor exposto pela API possui rótulo legível."""
    for enum_cls in enums.ALL_LABELED_ENUMS:
        for member in enum_cls:
            assert member.label and member.label != member.value
    for status in enums.SourceStatus:
        assert enums.AUDIO_STATUS_LABELS[status]
