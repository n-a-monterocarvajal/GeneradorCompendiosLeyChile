from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .generador import HEADERS

STATE_SCHEMA_VERSION = 1
JSON_URL = (
    "https://nuevo.leychile.cl/servicios/Navegar/get_norma_json"
    "?idNorma={id_norma}&idVersion=&idLey=&tipoVersion=&cve=&agrupa_partes=1&r={cache_buster}"
)


@dataclass(frozen=True)
class ReviewResult:
    baseline_created: bool
    changes_detected: bool
    changes: list[dict[str, Any]]
    state: dict[str, Any]


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return value if value is None or isinstance(value, (bool, int, float, str)) else str(value)


def _normalize_version(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    normalized = {
        "desde": _scalar(value.get("desde") or value.get("inicio_vigencia")),
        "hasta": _scalar(value.get("hasta") or value.get("fin_vigencia")),
        "tipo": _scalar(value.get("tipo_version")),
        "descripcion": _scalar(value.get("tipo_version_s")),
    }
    return normalized if any(normalized.values()) else None


def _normalize_deferred_alert(alerts: Any) -> dict[str, Any]:
    candidates = alerts if isinstance(alerts, list) else [alerts]
    deferred: list[dict[str, str]] = []
    for alert in candidates:
        if not isinstance(alert, dict):
            continue
        alert_class = _scalar(alert.get("clase"))
        if "diferido" not in alert_class.casefold():
            continue
        deferred.append({
            "clase": alert_class,
            "texto": _scalar(alert.get("texto")),
            "mensaje": _scalar(alert.get("mensaje")),
        })
    deferred.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return {"presente": bool(deferred), "alertas": deferred}


def normalize_snapshot(id_norma: str, marker: str, payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadatos") if isinstance(payload.get("metadatos"), dict) else {}
    current = _normalize_version(metadata.get("vigencia")) or {
        "desde": "", "hasta": "", "tipo": "", "descripcion": ""
    }
    current["tipo"] = _scalar(metadata.get("tipo_version"))
    current["descripcion"] = _scalar(metadata.get("tipo_version_s"))

    versions = [
        normalized for item in (metadata.get("vigencias") or [])
        if (normalized := _normalize_version(item)) is not None
    ]
    versions.sort(key=lambda item: (item["desde"], item["hasta"], item["tipo"], item["descripcion"]))

    stable = {
        "id_norma": str(id_norma),
        "marcador": marker,
        "titulo_bcn": _scalar(metadata.get("titulo_norma")),
        "vigencia_actual": current,
        "vigencias": versions,
        "fecha_version": _scalar(metadata.get("fecha_version")),
        "fecha_actualizacion_texto": _scalar(metadata.get("fecha_actualizacion_texto")),
        "eventos_pendientes": _canonical(metadata.get("eventos_pendientes") or []),
        "diferido": _normalize_deferred_alert(payload.get("alertas")),
    }
    canonical = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    stable["huella"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return stable


def fetch_snapshot(
    session: requests.Session,
    id_norma: str,
    marker: str,
    attempts: int = 3,
    base_wait: float = 2.0,
) -> dict[str, Any]:
    referer = f"https://www.bcn.cl/leychile/navegar?idNorma={id_norma}"
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            url = JSON_URL.format(id_norma=id_norma, cache_buster=int(time.time() * 1000))
            response = session.get(url, headers={"Referer": referer}, timeout=90)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"Respuesta transitoria HTTP {response.status_code}", response=response)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("metadatos"), dict):
                raise ValueError("Ley Chile respondió sin metadatos de norma.")
            return normalize_snapshot(id_norma, marker, payload)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(min(20.0, base_wait * (2 ** (attempt - 1)) + random.uniform(0, 1)))
    raise RuntimeError(f"No fue posible revisar idNorma={id_norma} después de {attempts} intentos.") from last_error


def build_state(
    config: dict[str, Any],
    fetcher: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if fetcher is None:
        session = requests.Session()
        session.headers.update(HEADERS | {"Accept": "application/json,text/plain;q=0.9,*/*;q=0.8"})
        try:
            sources = {
                str(source["id_norma"]): fetch_snapshot(
                    session, str(source["id_norma"]), str(source["marcador"])
                )
                for source in config["fuentes"]
            }
        finally:
            session.close()
    else:
        sources = {
            str(source["id_norma"]): fetcher(str(source["id_norma"]), str(source["marcador"]))
            for source in config["fuentes"]
        }
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "actualizado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fuentes": sources,
    }


def _version_key(version: dict[str, Any]) -> str:
    return json.dumps(version, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compare_states(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    old_sources = previous.get("fuentes") if isinstance(previous.get("fuentes"), dict) else {}
    new_sources = current.get("fuentes") if isinstance(current.get("fuentes"), dict) else {}

    for id_norma in sorted(set(old_sources) | set(new_sources), key=lambda value: int(value) if value.isdigit() else value):
        old = old_sources.get(id_norma)
        new = new_sources.get(id_norma)
        if old is None:
            changes.append({"id_norma": id_norma, "marcador": new.get("marcador", ""), "tipo": "fuente_agregada"})
            continue
        if new is None:
            changes.append({"id_norma": id_norma, "marcador": old.get("marcador", ""), "tipo": "fuente_eliminada"})
            continue
        if old.get("huella") == new.get("huella"):
            continue

        details: dict[str, Any] = {}
        old_versions = {_version_key(item): item for item in old.get("vigencias", [])}
        new_versions = {_version_key(item): item for item in new.get("vigencias", [])}
        added = [new_versions[key] for key in sorted(set(new_versions) - set(old_versions))]
        removed = [old_versions[key] for key in sorted(set(old_versions) - set(new_versions))]
        if added:
            details["vigencias_agregadas"] = added
        if removed:
            details["vigencias_eliminadas"] = removed
        for key, label in (
            ("vigencia_actual", "vigencia_actual"),
            ("diferido", "diferido"),
            ("eventos_pendientes", "eventos_pendientes"),
            ("fecha_version", "fecha_version"),
            ("fecha_actualizacion_texto", "fecha_actualizacion_texto"),
        ):
            if old.get(key) != new.get(key):
                details[label] = {"antes": old.get(key), "despues": new.get(key)}
        if not details:
            details["metadatos"] = {"antes": old.get("huella"), "despues": new.get("huella")}
        changes.append({
            "id_norma": id_norma,
            "marcador": new.get("marcador") or old.get("marcador") or "",
            "tipo": "version_actualizada",
            "detalles": details,
        })
    return changes


def review_versions(
    config: dict[str, Any],
    state_path: Path,
    fetcher: Callable[[str, str], dict[str, Any]] | None = None,
) -> ReviewResult:
    current = build_state(config, fetcher)
    if not state_path.exists():
        return ReviewResult(True, False, [], current)
    with state_path.open("r", encoding="utf-8-sig") as handle:
        previous = json.load(handle)
    if previous.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("La versión del esquema de estado no es compatible.")
    changes = compare_states(previous, current)
    return ReviewResult(False, bool(changes), changes, current)


def format_summary(result: ReviewResult) -> str:
    if result.baseline_created:
        return "Línea base de versiones creada; no se genera un compendio en esta primera revisión.\n"
    if not result.changes_detected:
        return "No se detectaron nuevas versiones ni cambios de vigencia o texto diferido.\n"

    lines = ["## Actualizaciones de normas detectadas", ""]
    for change in result.changes:
        marker = change.get("marcador") or "Norma sin título"
        id_norma = change["id_norma"]
        change_type = change["tipo"]
        lines.append(f"- **{marker}** (`idNorma={id_norma}`): {change_type.replace('_', ' ')}.")
        details = change.get("detalles", {})
        for version in details.get("vigencias_agregadas", []):
            label = version.get("descripcion") or version.get("tipo") or "versión"
            period = f"desde {version.get('desde') or '?'}"
            if version.get("hasta"):
                period += f" hasta {version['hasta']}"
            lines.append(f"  - Nueva vigencia: {label}, {period}.")
        if "vigencia_actual" in details:
            after = details["vigencia_actual"]["despues"] or {}
            lines.append(
                f"  - Vigencia actual: {after.get('descripcion') or after.get('tipo') or 'sin etiqueta'}, "
                f"desde {after.get('desde') or '?'} hasta {after.get('hasta') or 'sin término'}.")
        if "diferido" in details:
            present = bool((details["diferido"]["despues"] or {}).get("presente"))
            lines.append(f"  - Texto diferido: {'presente' if present else 'ya no informado'}.")
        if "eventos_pendientes" in details:
            lines.append("  - Cambiaron los eventos pendientes informados por Ley Chile.")
    return "\n".join(lines) + "\n"


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_github_output(changes_detected: bool, baseline_created: bool) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"changes_detected={'true' if changes_detected else 'false'}\n")
        handle.write(f"baseline_created={'true' if baseline_created else 'false'}\n")
