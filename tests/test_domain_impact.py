"""Recuperação de histórias potencialmente relacionadas a trechos novos (domain/impact, puro)."""

from app.domain.impact import cosine, find_candidates, strongest

STORIES = [
    {"id": 1, "titulo": "Emitir boletim", "historia": "Como coordenador, quero emitir o boletim da turma.",
     "criterios_aceitacao": []},
    {"id": 2, "titulo": "Cadastrar aluno", "historia": "Como secretaria, quero cadastrar novos alunos.",
     "criterios_aceitacao": [{"criterio": "Dado a matrícula, quando salvar, então o aluno aparece."}]},
    {"id": 3, "titulo": "Lançar notas", "historia": "Como professor, quero lançar as notas.", "criterios_aceitacao": []},
]
CHUNK = {"id": 10, "texto": "O boletim sai em PDF."}


def test_ranking_uses_position_not_threshold():
    vectors = {1: [1.0, 0.0], 2: [0.9, 0.1], 3: [0.0, 1.0]}
    candidates = find_candidates([CHUNK], STORIES, {10: [1.0, 0.0]}, vectors, {}, {}, [], top_k=2)
    assert set(candidates) == {1, 2}  # as duas mais próximas, mesmo com similaridade alta para ambas
    assert candidates[1].signals[0]["detalhe"].startswith("posição 1")


def test_functionality_and_term_signals_without_embeddings():
    candidates = find_candidates([CHUNK], STORIES, {}, {}, {10: {"FN1"}}, {"FN1": {3, 99}}, ["boletim", "PDF", "a"], 0)
    assert set(candidates) == {1, 3}  # FN1 → história 3 (99 não está ativa); termo "boletim" → história 1
    assert [s["sinal"] for s in candidates[3].signals] == ["funcionalidade"]
    assert [s["detalhe"] for s in candidates[1].signals] == ["boletim"]  # "PDF" não aparece na história


def test_strongest_prefers_functionality_link():
    candidates = find_candidates([CHUNK], STORIES, {10: [1.0, 0.0]}, {1: [1.0, 0.0], 2: [0.9, 0.1], 3: [0.0, 1.0]},
                                 {10: {"FN1"}}, {"FN1": {3}}, [], top_k=2)
    chosen, rest = strongest(list(candidates.values()), 2)
    assert [c.story_id for c in chosen] == [3, 1] and [c.story_id for c in rest] == [2]


def test_cosine():
    assert cosine([1, 0], [1, 0]) == 1.0 and cosine([1, 0], [0, 1]) == 0.0 and cosine([0, 0], [1, 1]) == 0.0
