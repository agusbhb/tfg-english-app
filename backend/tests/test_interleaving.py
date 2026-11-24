from itertools import groupby

import pytest
from test_modalities import question

from app.bank_contracts import (
    MODALITIES,
    MODALITY_WEIGHTS,
    GeneratedBankQuestion,
    modality_counts,
    modality_distribution,
)
from app.generation import interleave_modalities


def grouped(quotas):
    return [
        GeneratedBankQuestion.model_validate(question(mode, index))
        for mode, count in quotas.items()
        for index in range(count)
    ]


def test_thirty_have_seven_two_one_in_each_ten_and_no_long_runs():
    questions = grouped({"afirmativa": 21, "negativa": 6, "interrogativa": 3})
    ordered = interleave_modalities(questions)
    for offset in range(0, 30, 10):
        assert modality_counts(ordered[offset : offset + 10]) == {
            "afirmativa": 7,
            "negativa": 2,
            "interrogativa": 1,
        }
    assert max(len(list(run)) for _, run in groupby(q.modalidad for q in ordered)) == 3


@pytest.mark.parametrize("quantity", [1, 2, 3, 5, 10, 13, 30, 31, 50])
def test_sizes_zero_quotas_stability_and_entire_questions_unchanged(quantity):
    quotas = modality_distribution(quantity, "present-simple", "A1")
    questions = grouped(quotas)
    original = [q.model_dump() for q in questions]
    ordered = interleave_modalities(questions)
    assert modality_counts(ordered) == quotas
    assert len(ordered) == len(questions)
    assert {id(q) for q in ordered} == {id(q) for q in questions}
    assert ordered == interleave_modalities(questions)
    # También es estable si se aplica de nuevo a una salida ya intercalada.
    assert ordered == interleave_modalities(ordered)
    assert [q.model_dump() for q in questions] == original
    for mode in MODALITIES:
        assert [q for q in ordered if q.modalidad == mode] == [
            q for q in questions if q.modalidad == mode
        ]


@pytest.mark.parametrize("weights", [(40, 30, 30), (0, 50, 50), (0, 0, 100)])
def test_uses_the_validated_topic_level_quota_not_a_universal_ratio(monkeypatch, weights):
    monkeypatch.setitem(MODALITY_WEIGHTS, ("other-topic", "A2"), weights)
    quotas = modality_distribution(13, "other-topic", "A2")
    assert modality_counts(interleave_modalities(grouped(quotas))) == quotas


def test_ties_have_fixed_order_and_empty_batch_is_safe():
    questions = grouped({"interrogativa": 1, "negativa": 1, "afirmativa": 1})
    assert tuple(q.modalidad for q in interleave_modalities(questions)) == MODALITIES
    assert interleave_modalities([]) == []


def test_unlabelled_legacy_questions_are_not_silently_dropped():
    legacy = question("afirmativa", 1)
    legacy.pop("modalidad")
    with pytest.raises(ValueError, match="modalidad válida"):
        interleave_modalities([GeneratedBankQuestion.model_validate(legacy)])
