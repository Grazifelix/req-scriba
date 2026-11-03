import pytest

from app.domain import qus

EXPECTED = {1: 7.69, 2: 15.38, 3: 23.08, 4: 30.77, 5: 38.46, 6: 46.15, 7: 53.85,
            8: 61.54, 9: 69.23, 10: 76.92, 11: 84.62, 12: 92.31, 13: 100.0}


@pytest.mark.parametrize("met,expected", sorted(EXPECTED.items()))
def test_percentage_matches_prd_table(met, expected):
    assert qus.percentage(met) == expected


def test_total_criteria_is_13():
    assert qus.TOTAL_CRITERIA == 13
    assert len(qus.INDIVIDUAL_CRITERIA) == 8 and len(qus.SET_CRITERIA) == 5


def test_percentage_rejects_out_of_range():
    with pytest.raises(ValueError):
        qus.percentage(14)


@pytest.mark.parametrize("score,label", [
    (0, "Péssima"), (24.99, "Péssima"), (25, "Baixa"), (49.99, "Baixa"), (50, "Regular"),
    (74.99, "Regular"), (75, "Alta"), (99.99, "Alta"), (100, "Excelente"), (None, "Sem avaliação"),
])
def test_quality_bands(score, label):
    assert qus.quality_label(score) == label


def test_band_for_each_criteria_count():
    assert qus.quality_label(qus.percentage(3)) == "Péssima"   # 23.08
    assert qus.quality_label(qus.percentage(4)) == "Baixa"     # 30.77
    assert qus.quality_label(qus.percentage(7)) == "Regular"   # 53.85
    assert qus.quality_label(qus.percentage(12)) == "Alta"     # 92.31


@pytest.mark.parametrize("raw,expected", [
    ("Atende", qus.MEETS), ("atende", qus.MEETS), ("ATENDE plenamente", qus.MEETS), ("Sim", qus.MEETS),
    ("Não atende", qus.FAILS), ("nao atende", qus.FAILS), ("Violado", qus.FAILS),
    ("Atenção", qus.ATTENTION), ("Parcial", qus.ATTENTION), ("???", qus.ATTENTION), (None, qus.ATTENTION),
])
def test_normalize_result(raw, expected):
    assert qus.normalize_result(raw) == expected


def test_count_met_only_counts_atende():
    individual = {k: {"resultado": qus.MEETS} for k in qus.INDIVIDUAL_CRITERIA}
    individual["atomic"] = {"resultado": qus.ATTENTION}
    set_analysis = {k: {"resultado": qus.FAILS} for k in qus.SET_CRITERIA}
    set_analysis["unique"] = {"resultado": qus.MEETS}
    assert qus.count_met(individual, set_analysis) == 8
