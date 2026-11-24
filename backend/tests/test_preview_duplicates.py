import asyncio
import copy

import pytest
from bson import ObjectId

from app.generation import GenerationResponse, sentence_key
from app.models import AIModel
from scripts.data_snapshot import snapshot
from scripts.preview_generation import preview


@pytest.mark.parametrize(
    "left,right",
    [
        (" I ___ (play) football. ", "i ___ (play) football."),
        ("She\t___  (watch)\nTV.", "she ___ (watch) tv."),
        ("They\u00a0___ (study).", "they ___ (study)."),
    ],
)
def test_sentence_identity_ignores_case_and_whitespace(left, right):
    assert sentence_key(left) == sentence_key(right)


class SimulatedProvider:
    model = AIModel(proveedor="simulado", modelo="solo-test")

    def __init__(self, questions):
        self.questions = questions
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return GenerationResponse(payload={"preguntas": self.questions})


@pytest.mark.integration
@pytest.mark.parametrize("duplicates", [5, 2, 0])
def test_preview_counts_existing_sentences_with_different_distractors(api, duplicates):
    _, database, settings = api
    originals = list(database.BancoEjercicios.find().sort("secuencia", 1).limit(5))
    assert all(question["distractores"] == [] for question in originals)
    generated = [
        {
            "enunciado": " \t" + "  ".join(question["enunciado"].upper().split()) + "\n ",
            "respuesta_principal": question["respuesta_principal"],
            "solucion": question["solucion"],
            "distractores": ["never-accepted", "also-wrong"],
        }
        for question in originals[:duplicates]
    ]
    generated += [
        {
            "enunciado": f"Every morning, learner {index} ___ (play) football.",
            "respuesta_principal": "plays",
            "solucion": {"respuestas_aceptadas": ["plays"], "sensible_mayusculas": False},
            "distractores": ["play", "playing"],
        }
        for index in range(5 - duplicates)
    ]
    # Una frase en OTRO banco sigue siendo candidata en el banco consultado.
    if duplicates < 5:
        bank = copy.deepcopy(database.Bancos.find_one())
        bank["_id"] = ObjectId()
        bank["nivel_mcer_objetivo"] = bank["ficha"]["nivel_mcer"] = "A2"
        database.Bancos.insert_one(bank)
        foreign = copy.deepcopy(originals[0])
        foreign.update(_id=ObjectId(), banco_id=bank["_id"], secuencia=1, **generated[-1])
        database.BancoEjercicios.insert_one(foreign)
    provider = SimulatedProvider(generated)
    before = snapshot(settings)
    report = asyncio.run(preview(settings, provider, 5))
    assert len(provider.requests) == 1
    assert report["estado"] == "solo_revision_sin_publicar"
    assert report["preguntas_recibidas"] == 5
    assert report["preguntas_descartadas_duplicadas"] == duplicates
    assert report["preguntas_candidatas"] == len(report["preguntas"]) == 5 - duplicates
    original_keys = {sentence_key(question["enunciado"]) for question in originals}
    assert all(
        sentence_key(question["enunciado"]) not in original_keys for question in report["preguntas"]
    )
    if duplicates == 5:
        assert report["preguntas"] == []
    assert snapshot(settings) == before
