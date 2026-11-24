"""Una generación real para revisión. Solo lee MongoDB; nunca publica preguntas."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import PyMongoError

from app.anthropic import AnthropicError, AnthropicProvider
from app.bank_contracts import BankMetadataDocument, modality_counts
from app.config import Settings
from app.database import create_client
from app.generation import collect_generation_batch


def text_capture(directory, records):
    """Guardar solo contenido textual, nunca el sobre HTTP ni cabeceras."""

    def capture(text, diagnostic):
        record = {"ronda": diagnostic["ronda"]}
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"anthropic-ronda-{diagnostic['ronda']}-{uuid4().hex}.txt"
            with path.open("x", encoding="utf-8") as output:
                output.write(text)
            record["texto_guardado"] = str(path)
        except OSError:
            record["aviso_guardado"] = "No se pudo guardar el texto del modelo en .tools"
        records.append(record)
        return record

    return capture


async def preview(settings, provider, quantity=5, complete_batch=False):
    if settings.bank_refill_enabled:
        raise ValueError("Mantén BANK_REFILL_ENABLED=false durante el ensayo manual")
    client = create_client(settings)
    try:
        database = client[settings.mongodb_db]
        banks = (
            await database.Bancos.find(
                {
                    "codigo_tema": "present-simple",
                    "nivel_mcer_objetivo": "A1",
                }
            )
            .limit(2)
            .to_list()
        )
        if len(banks) != 1:
            raise ValueError("El ensayo requiere un único banco Present Simple A1 existente")
        bank = BankMetadataDocument.model_validate(banks[0])
        async with asyncio.timeout(settings.bank_refill_timeout_seconds):
            batch = await collect_generation_batch(
                database,
                bank,
                provider,
                quantity,
                ObjectId(),
                max_rounds=settings.bank_generation_max_rounds if complete_batch else 1,
            )
        response = batch.responses[-1]
        return {
            "estado": (
                "lote_incompleto_sin_publicar"
                if complete_batch and not batch.complete
                else "solo_revision_sin_publicar"
            ),
            "modelo_solicitado": provider.model.modelo,
            "modelo_utilizado": response.modelo_utilizado,
            "proveedor": response.proveedor_utilizado,
            "id_llamada": response.id_llamada,
            "tokens_entrada": batch.total("tokens_entrada"),
            "tokens_salida": batch.total("tokens_salida"),
            "prompt_version": batch.trace.prompt_version,
            "preguntas_recibidas": batch.received,
            "preguntas_descartadas_duplicadas": batch.duplicate_count,
            "preguntas_rechazadas_contrato": batch.rejected,
            "rechazos_contrato": [
                {"ronda": index + 1, **rejection}
                for index, round_ in enumerate(batch.rounds)
                for rejection in round_["rechazos_contrato"]
            ],
            "preguntas_descartadas_cuota": sum(r["descartadas_cuota"] for r in batch.rounds),
            "preguntas_candidatas": len(batch.questions),
            "candidatas_primera_ronda": batch.rounds[0]["candidatas_acumuladas"],
            "duplicadas_primera_ronda": batch.rounds[0]["duplicadas"],
            "numero_llamadas": len(batch.responses),
            "lote_completo": batch.complete,
            "distribucion_objetivo": batch.distribution,
            "distribucion_final": modality_counts(batch.questions)
            if batch.trace.prompt_version >= 4
            else None,
            "rondas": batch.rounds,
            "preguntas": [question.model_dump() for question in batch.questions],
        }
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cantidad", type=int, default=5, choices=range(1, 51))
    parser.add_argument("--modo", choices=("diagnostico", "lote"), default="diagnostico")
    parser.add_argument(
        "--guardar-texto",
        action="store_true",
        help="Guardar únicamente el texto Anthropic en .tools (éxito o error de validación)",
    )
    args = parser.parse_args()
    captures = []
    try:
        settings = Settings()
        provider = AnthropicProvider
        options = (
            {"capture_text": text_capture(Path.cwd() / ".tools", captures)}
            if args.guardar_texto
            else {}
        )
        report = asyncio.run(
            preview(
                settings,
                provider(settings, **options),
                args.cantidad,
                complete_batch=args.modo == "lote",
            )
        )
    except AnthropicError as error:
        if getattr(error, "diagnostic", None) is not None:
            print(
                json.dumps(
                    {
                        "estado": "fallo_generacion_sin_publicar",
                        "mensaje": str(error),
                        "diagnostico": error.diagnostic,
                        "textos_guardados": captures,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1) from None
        raise SystemExit(str(error)) from None
    except (ValueError, PyMongoError, TimeoutError):
        raise SystemExit(
            "No se pudo preparar el ensayo: revisa configuración, MongoDB, "
            "banco, ficha y prompt activo; "
            "BANK_REFILL_ENABLED debe ser false. No se ha publicado contenido."
        ) from None
    if args.guardar_texto:
        report["textos_guardados"] = captures
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
