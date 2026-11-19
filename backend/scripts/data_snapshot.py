"""Inventario de solo lectura antes/después del cambio de .env, sin datos privados."""

import argparse
import hashlib
import json
import sys

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import Settings


def digest(value):
    return hashlib.sha256(json_util.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot(settings):
    with MongoClient(
        settings.mongodb_uri.get_secret_value(),
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        timeoutMS=10000,
    ) as client:
        hello = client.admin.command("hello")
        database = client[settings.mongodb_db]
        collections = {}
        for name in sorted(database.list_collection_names()):
            documents = list(database[name].find().sort("_id", 1))
            collections[name] = {
                "documentos": len(documents),
                "sha256_documentos": digest(documents),
                "sha256_indices": digest(database[name].index_information()),
            }
        return {
            "base": settings.mongodb_db,
            "replica_set": hello.get("setName"),
            "colecciones": collections,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-stdin", action="store_true")
    args = parser.parse_args()
    try:
        current = snapshot(Settings())
        if args.compare_stdin:
            before = json.loads(sys.stdin.read().lstrip("\ufeff"))
            if current != before:
                raise SystemExit(
                    "El inventario ha cambiado. Detén la adopción y revisa la copia; "
                    "no se ha escrito en MongoDB."
                )
            print(
                "Verificado: misma base, replica set, documentos e índices "
                "de todas las colecciones."
            )
        else:
            print(json.dumps(current, ensure_ascii=False, indent=2))
    except (ValueError, PyMongoError):
        raise SystemExit(
            "No se pudo leer el inventario. Revisa MongoDB y la configuración local."
        ) from None


if __name__ == "__main__":
    main()
