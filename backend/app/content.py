"""Fichas versionadas conocidas por el servidor, independientes del curso escolar."""

from hashlib import sha256
from pathlib import Path

from pydantic import Field, model_validator

from app.bank_contracts import ContentKey, ContentReference
from app.models import Model

CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"
SHEETS = {("present-simple", "A1", 1): "ingles/present-simple/A1/v1.md"}
SHEETS[("present-simple", "A1", 2)] = "ingles/present-simple/A1/v2.md"


class EducationalSheet(Model):
    referencia: ContentReference
    contenido: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def matching_hash(self):
        if sha256(self.contenido.encode("utf-8")).hexdigest() != self.referencia.sha256:
            raise ValueError("El hash no corresponde al contenido de la ficha")
        return self


def load_educational_sheet(key: ContentKey) -> EducationalSheet:
    relative = SHEETS.get((key.codigo_tema, key.nivel_mcer, key.version))
    if relative is None:
        raise ValueError("No existe una ficha aprobada para ese tema, nivel y versión")
    # El cliente nunca proporciona rutas. read_text normaliza CRLF/LF para el hash.
    content = (CONTENT_ROOT / relative).read_text(encoding="utf-8")
    return EducationalSheet(
        referencia=ContentReference(
            **key.model_dump(), sha256=sha256(content.encode("utf-8")).hexdigest()
        ),
        contenido=content,
    )
