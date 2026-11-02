"""Comprueba que el XML de pytest-cov se puede resolver en el checkout de Sonar."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath


def check_coverage(report: Path, repository: Path) -> None:
    root = ET.parse(report).getroot()
    sources = [node.text for node in root.findall("sources/source")]
    if sources != ["backend/app"]:
        raise ValueError(f"Se esperaba la fuente relativa backend/app; recibido: {sources}")

    classes = root.findall("packages/package/classes/class")
    if not classes or int(root.attrib.get("lines-valid", "0")) <= 0:
        raise ValueError("El informe no contiene archivos y líneas medidos")

    source = (repository / "backend/app").resolve()
    for node in classes:
        filename = PurePosixPath(node.attrib["filename"])
        resolved = (source / filename).resolve()
        if filename.is_absolute() or not resolved.is_relative_to(source) or not resolved.is_file():
            raise ValueError(f"Archivo de cobertura fuera del backend o inexistente: {filename}")

    print(f"Cobertura válida: {len(classes)} archivos resueltos en backend/app")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python scripts/check_coverage.py coverage.xml")
    try:
        check_coverage(Path(sys.argv[1]), Path(__file__).resolve().parents[1])
    except (ET.ParseError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"Informe de cobertura inválido: {exc}") from exc
