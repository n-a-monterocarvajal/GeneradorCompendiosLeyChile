from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ALLOWED_BCN_HOSTS = {"bcn.cl", "www.bcn.cl", "leychile.cl", "www.leychile.cl"}


def safe_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", ascii_value).strip("_") or "archivo"


def read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        config = json.load(handle)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValueError("La configuración debe ser un objeto JSON.")
    if not str(config.get("titulo_compendio") or "").strip():
        raise ValueError("Falta titulo_compendio.")
    if not str(config.get("salida_base") or "").strip():
        raise ValueError("Falta salida_base.")

    sources = config.get("fuentes")
    if not isinstance(sources, list) or not sources:
        raise ValueError("fuentes debe ser una lista no vacía.")

    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise ValueError(f"La fuente {index} debe ser un objeto.")
        if source.get("tipo") != "bcn":
            raise ValueError(f"Tipo de fuente no soportado en la fuente {index}: {source.get('tipo')}")

        id_norma = str(source.get("id_norma") or "").strip()
        if not id_norma.isdigit():
            raise ValueError(f"id_norma inválido en la fuente {index}: {id_norma!r}")
        if id_norma in seen_ids:
            raise ValueError(f"id_norma duplicado: {id_norma}")
        seen_ids.add(id_norma)

        marker = str(source.get("marcador") or "").strip()
        if not marker:
            raise ValueError(f"Falta marcador en la fuente {index}.")

        file_stub = str(source.get("archivo") or safe_name(marker)).strip()
        if file_stub != safe_name(file_stub):
            raise ValueError(f"archivo inseguro en la fuente {index}: {file_stub!r}")
        if file_stub in seen_files:
            raise ValueError(f"archivo duplicado: {file_stub}")
        seen_files.add(file_stub)

        url = str(source.get("url") or f"https://www.bcn.cl/leychile/navegar?idNorma={id_norma}")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_BCN_HOSTS:
            raise ValueError(f"URL BCN inválida en la fuente {index}: {url}")
        url_id = (parse_qs(parsed.query).get("idNorma") or [""])[0]
        if url_id != id_norma:
            raise ValueError(f"La URL de la fuente {index} no coincide con id_norma={id_norma}.")

    download = config.get("descarga", {})
    if not isinstance(download, dict):
        raise ValueError("descarga debe ser un objeto cuando está presente.")
    _validate_int(download, "reintentos_por_norma", 1, 6)
    _validate_int(download, "reiniciar_contexto_cada", 1, 50)
    _validate_number(download, "espera_base_segundos", 0, 60)


def _validate_int(config: dict[str, Any], key: str, minimum: int, maximum: int) -> None:
    if key not in config:
        return
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{key} debe ser un entero entre {minimum} y {maximum}.")


def _validate_number(config: dict[str, Any], key: str, minimum: float, maximum: float) -> None:
    if key not in config:
        return
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ValueError(f"{key} debe estar entre {minimum} y {maximum}.")
