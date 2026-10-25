from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .generador import HEADERS

STATE_SCHEMA_VERSION = 1
RELEVANCIA_SIGNIFICATIVA = "significativa"
RELEVANCIA_INFORMATIVA = "informativa"
JSON_URL = (
    "https://nuevo.leychile.cl/servicios/Navegar/get_norma_json"
    "?idNorma={id_norma}&idVersion=&idLey=&tipoVersion=&cve=&agrupa_partes=1&r={cache_buster}"
)
# Servicios que usa la misma página de Ley Chile para listar las versiones de una norma con
# sus normas modificatorias y para identificar la última modificatoria de una versión.
VERSIONES_URL = (
    "https://nuevo.leychile.cl/servicios/Consulta/get_versiones"
    "?idNorma={id_norma}&formato=json&idParte=&idsParte="
)
ULTIMA_MODIFICATORIA_URL = (
    "https://nuevo.leychile.cl/servicios/Navegar/get_ultima_modificatoria"
    "?idNorma={id_norma}&idVersion={fecha}&idParte="
)
# Fecha que Ley Chile usa como inicio de las vigencias diferidas sin fecha conocida. La
# vigencia anterior a una de ellas termina, en los datos, el día previo (2222-02-01).
FECHA_SIN_DETERMINAR = "2222-02-02"
_ANIO_CENTINELA = "2222-"


@dataclass(frozen=True)
class ReviewResult:
    baseline_created: bool
    changes_detected: bool
    changes: list[dict[str, Any]]
    state: dict[str, Any]
    state_migrated: bool = False

    @property
    def significant_changes(self) -> list[dict[str, Any]]:
        """Cambios que justifican regenerar y publicar el compendio."""
        return [
            change for change in self.changes
            if change.get("relevancia", RELEVANCIA_SIGNIFICATIVA) != RELEVANCIA_INFORMATIVA
        ]

    @property
    def informational_changes(self) -> list[dict[str, Any]]:
        """Cambios registrados por trazabilidad que no alteran el texto vigente."""
        return [
            change for change in self.changes
            if change.get("relevancia") == RELEVANCIA_INFORMATIVA
        ]

    @property
    def status_warning_changes(self) -> list[dict[str, Any]]:
        """Normas que pasaron a derogadas o refundidas: alguien debe decidir si siguen."""
        return [change for change in self.changes if status_warnings(change)]

    @property
    def state_updated(self) -> bool:
        """El estado persistente debe reescribirse, aunque no se publique nada."""
        return self.baseline_created or self.state_migrated or bool(self.changes)


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
    indicated = indicated_current_version(id_norma, payload.get("alerta_global"))
    stable["vigencia_actual"] = _effective_current(current, versions, indicated)
    stable["huella"] = _fingerprint(stable)
    # Fuera de la huella: así, agregar estos datos no hace parecer cambiadas todas las normas
    # de un estado guardado con una versión anterior del motor.
    stable["estado"] = _normalize_status(metadata, payload.get("alertas"))
    stable["version_vigente_indicada"] = indicated
    return stable


_FINGERPRINT_KEYS = (
    "id_norma", "marcador", "titulo_bcn", "vigencia_actual", "vigencias", "fecha_version",
    "fecha_actualizacion_texto", "eventos_pendientes", "diferido",
)


def _fingerprint(snapshot: dict[str, Any]) -> str:
    stable = {key: snapshot.get(key) for key in _FINGERPRINT_KEYS}
    canonical = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _effective_current(
    current: dict[str, str], versions: list[dict[str, str]], indicated: str
) -> dict[str, str]:
    """Vigencia actual que publica el compendio.

    Cuando Ley Chile sirve por defecto una versión que ya no está vigente y avisa cuál sí lo
    está, el compendio descarga esta última. Ley Chile alterna por días entre ambas respuestas;
    si la vigencia actual siguiera a la versión por defecto, cada alternancia parecería una
    versión nueva y publicaría un compendio con el mismo texto.
    """
    if not indicated or current.get("desde") == indicated:
        return current
    for version in versions:
        if version.get("desde") == indicated:
            return dict(version)
    return current


def effective_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Aplica la vigencia efectiva a una norma guardada por un motor anterior a 0.1.8."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("vigencia_actual"), dict):
        return snapshot
    versions = [item for item in snapshot.get("vigencias") or [] if isinstance(item, dict)]
    effective = _effective_current(
        snapshot["vigencia_actual"], versions, _scalar(snapshot.get("version_vigente_indicada"))
    )
    if effective == snapshot["vigencia_actual"]:
        return snapshot
    updated = dict(snapshot, vigencia_actual=effective)
    updated["huella"] = _fingerprint(updated)
    return updated


_STATUS_ALERT_CLASSES = ("derogado", "refundido")


def _normalize_status(metadata: dict[str, Any], alerts: Any) -> dict[str, Any]:
    """Estado de la norma como fuente: derogación expresa o tácita y refundición en otra norma."""
    candidates = alerts if isinstance(alerts, list) else [alerts]
    found: list[dict[str, str]] = []
    for alert in candidates:
        if not isinstance(alert, dict):
            continue
        alert_class = _scalar(alert.get("clase")).casefold()
        if alert_class in _STATUS_ALERT_CLASSES:
            found.append({"clase": alert_class, "mensaje": _scalar(alert.get("mensaje"))})
    found.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return {
        "derogado": _scalar(metadata.get("derogado")).casefold() == "true",
        "derogacion_tacita": _canonical(metadata.get("derogacion_tacita") or []),
        "alertas": found,
    }


def indicated_current_version(id_norma: str, global_alert: Any) -> str:
    """Fecha de la versión vigente cuando Ley Chile sirve por defecto otra que ya no lo está.

    Ley Chile puede tardar en mover su versión por defecto a la que entró en vigencia. Mientras
    tanto, la página muestra el aviso «El texto de esta versión no se encuentra vigente» y un
    enlace a la versión vigente, cuya fecha viene en ``alerta_global.e.f``.
    """
    if not isinstance(global_alert, dict):
        return ""
    target = global_alert.get("e")
    if not isinstance(target, dict):
        return ""
    version = _scalar(target.get("f"))
    if _scalar(target.get("i")) not in ("", str(id_norma)):
        return ""
    try:
        date.fromisoformat(version)
    except ValueError:
        return ""
    return version


class RevisionFallida(RuntimeError):
    """No fue posible revisar una norma después de agotar los intentos."""

    tipo = "error"


class FallaTransitoriaLeyChile(RevisionFallida):
    """Ley Chile no respondió: timeout, conexión, HTTP 429 o 5xx. Se espera que pase sola."""

    tipo = "transitoria"


class FormatoInesperadoLeyChile(RevisionFallida):
    """Ley Chile respondió, pero no con la forma que la revisión sabe leer."""

    tipo = "formato"


class RespuestaNoJson(ValueError):
    """Ley Chile respondió con un cuerpo vacío o una página HTML en vez de JSON.

    Es lo que entrega el sitio durante sus mantenciones (pasó el 25-oct-2026 en los tres
    compendios durante unos minutos), así que se trata como falla transitoria. Si se
    repite varios días seguidos, los workflows de los compendios abren igual un issue.
    """


def _describe_response(response: Any) -> str:
    headers = getattr(response, "headers", None) or {}
    content_type = headers.get("Content-Type") or headers.get("content-type") or "sin Content-Type"
    text = getattr(response, "text", "") or ""
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    detail = f"HTTP {response.status_code}, {content_type}, {len(text.strip())} caracteres"
    if title and title.group(1).strip():
        detail += f", título «{' '.join(title.group(1).split())[:120]}»"
    return detail


def _parse_json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError as error:
        text = (getattr(response, "text", "") or "").lstrip()
        if not text or text.startswith("<"):
            raise RespuestaNoJson(
                f"Ley Chile respondió sin JSON ({_describe_response(response)})."
            ) from error
        raise ValueError(f"Ley Chile respondió con JSON ilegible ({_describe_response(response)}).") from error


# Señales que todas las normas de los compendios informan hoy. Si alguna falta, la
# respuesta cambió de forma y la huella dejaría de ser comparable con la anterior: se
# trata como falla de lectura en vez de reportarla como una actualización normativa.
_REQUIRED_METADATA = ("titulo_norma", "tipo_version", "fecha_version")


def _validate_payload(payload: Any) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("metadatos"), dict):
        raise ValueError("Ley Chile respondió sin metadatos de norma.")
    metadata = payload["metadatos"]
    missing = [key for key in _REQUIRED_METADATA if not _scalar(metadata.get(key))]
    current = _normalize_version(metadata.get("vigencia"))
    if current is None or not current["desde"]:
        missing.append("vigencia")
    versions = metadata.get("vigencias")
    if not isinstance(versions, list) or not any(_normalize_version(item) for item in versions):
        missing.append("vigencias")
    if missing:
        raise ValueError(f"Ley Chile respondió sin {', '.join(missing)} en los metadatos.")


def _is_transient(error: Exception) -> bool:
    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return error.response.status_code == 429 or error.response.status_code >= 500
    return isinstance(error, RespuestaNoJson)


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
            payload = _parse_json(response)
            _validate_payload(payload)
            return normalize_snapshot(id_norma, marker, payload)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(min(20.0, base_wait * (2 ** (attempt - 1)) + random.uniform(0, 1)))
    failure = FallaTransitoriaLeyChile if last_error is not None and _is_transient(last_error) else FormatoInesperadoLeyChile
    raise failure(
        f"No fue posible revisar idNorma={id_norma} después de {attempts} intentos: {last_error}"
    ) from last_error


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


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(_scalar(value)[:10])
    except ValueError:
        return None


def _is_historical(version: Any, current_start: str, today: date) -> bool:
    """Indica si una vigencia corresponde a un período ya cerrado.

    Ante cualquier duda se responde ``False``: una vigencia sin término, con fecha
    ilegible o que comparte inicio con la vigencia actual se trata como vigente.
    """
    if not isinstance(version, dict):
        return False
    start = _scalar(version.get("desde"))
    if not start or start == current_start:
        return False
    end = _parse_date(version.get("hasta"))
    return end is not None and end < today


_SIGNIFICANT_DETAIL_KEYS = (
    "vigencias_agregadas",
    "vigencias_eliminadas",
    "vigencia_actual",
    "diferido",
    "eventos_pendientes",
    "fecha_version",
    "fecha_actualizacion_texto",
    "estado_norma",
    "metadatos",
)


def classify_relevance(details: dict[str, Any], current: Any, today: date) -> str:
    """Distingue cambios que afectan el texto vigente de reclasificaciones históricas.

    Ley Chile corrige con cierta frecuencia los límites de fecha entre versiones ya
    superadas. Ese ajuste no altera el texto que se descarga hoy, de modo que se
    registra en el estado pero no justifica regenerar el compendio.
    """
    if any(key in details for key in _SIGNIFICANT_DETAIL_KEYS):
        return RELEVANCIA_SIGNIFICATIVA
    updated = details.get("vigencias_actualizadas") or []
    if not updated:
        return RELEVANCIA_SIGNIFICATIVA
    current_start = _scalar(current.get("desde")) if isinstance(current, dict) else ""
    if not current_start:
        return RELEVANCIA_SIGNIFICATIVA
    for update in updated:
        if not _is_historical(update.get("antes"), current_start, today):
            return RELEVANCIA_SIGNIFICATIVA
        if not _is_historical(update.get("despues"), current_start, today):
            return RELEVANCIA_SIGNIFICATIVA
    return RELEVANCIA_INFORMATIVA


def _version_key(version: dict[str, Any]) -> str:
    return json.dumps(version, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pair_versions(
    key: str,
    old_versions: list[dict[str, Any]],
    new_versions: list[dict[str, Any]],
    paired_old: set[int],
    paired_new: set[int],
    updated: list[dict[str, Any]],
    allow_empty: bool = False,
) -> None:
    """Empareja los registros pendientes usando ``key`` como identidad estable.

    Si Ley Chile entrega más de un registro con el mismo valor, los pares se eligen
    por menor cantidad de campos modificados y luego por su representación canónica
    para mantener un resultado determinista.
    """
    fields = ("desde", "hasta", "tipo", "descripcion")
    old_by_key: dict[str, list[dict[str, Any]]] = {}
    new_by_key: dict[str, list[dict[str, Any]]] = {}
    for version in old_versions:
        if id(version) in paired_old:
            continue
        value = _scalar(version.get(key))
        if value or allow_empty:
            old_by_key.setdefault(value, []).append(version)
    for version in new_versions:
        if id(version) in paired_new:
            continue
        value = _scalar(version.get(key))
        if value or allow_empty:
            new_by_key.setdefault(value, []).append(version)

    for value in sorted(set(old_by_key) & set(new_by_key)):
        candidates = []
        for old in old_by_key[value]:
            for new in new_by_key[value]:
                changed_fields = [field for field in fields if old.get(field) != new.get(field)]
                candidates.append((
                    len(changed_fields),
                    _version_key(old),
                    _version_key(new),
                    old,
                    new,
                    changed_fields,
                ))
        for _, _, _, old, new, changed_fields in sorted(candidates, key=lambda item: item[:3]):
            if id(old) in paired_old or id(new) in paired_new:
                continue
            paired_old.add(id(old))
            paired_new.add(id(new))
            updated.append({
                "antes": old,
                "despues": new,
                "campos_modificados": changed_fields,
            })


def _compare_versions(
    old_values: Any,
    new_values: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa vigencias agregadas, eliminadas y actualizadas.

    Primero descarta coincidencias exactas. Entre los registros restantes se empareja
    primero por ``desde`` y después por ``hasta``: cuando Ley Chile corre el límite
    entre dos vigencias contiguas, la primera conserva su inicio y la segunda su
    término, de modo que ambas se reconocen como actualizaciones del mismo tramo y
    no como la desaparición y aparición de vigencias distintas.
    """
    old_versions = sorted(
        (item for item in old_values or [] if isinstance(item, dict)),
        key=_version_key,
    )
    new_versions = sorted(
        (item for item in new_values or [] if isinstance(item, dict)),
        key=_version_key,
    )

    old_exact: dict[str, list[dict[str, Any]]] = {}
    for version in old_versions:
        old_exact.setdefault(_version_key(version), []).append(version)

    unmatched_new: list[dict[str, Any]] = []
    for version in new_versions:
        matches = old_exact.get(_version_key(version))
        if matches:
            matches.pop()
        else:
            unmatched_new.append(version)
    unmatched_old = [version for matches in old_exact.values() for version in matches]

    paired_old: set[int] = set()
    paired_new: set[int] = set()
    updated: list[dict[str, Any]] = []
    _pair_versions("desde", unmatched_old, unmatched_new, paired_old, paired_new, updated)
    _pair_versions(
        "hasta", unmatched_old, unmatched_new, paired_old, paired_new, updated, allow_empty=True
    )

    added = sorted(
        (version for version in unmatched_new if id(version) not in paired_new),
        key=_version_key,
    )
    removed = sorted(
        (version for version in unmatched_old if id(version) not in paired_old),
        key=_version_key,
    )
    updated.sort(key=lambda item: (
        _scalar(item["despues"].get("desde")),
        _version_key(item["antes"]),
        _version_key(item["despues"]),
    ))
    return added, removed, updated


def compare_states(
    previous: dict[str, Any],
    current: dict[str, Any],
    today: date | None = None,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    reference_day = today or datetime.now(timezone.utc).date()
    old_sources = previous.get("fuentes") if isinstance(previous.get("fuentes"), dict) else {}
    new_sources = current.get("fuentes") if isinstance(current.get("fuentes"), dict) else {}

    for id_norma in sorted(set(old_sources) | set(new_sources), key=lambda value: int(value) if value.isdigit() else value):
        old = old_sources.get(id_norma)
        new = new_sources.get(id_norma)
        if old is None:
            changes.append({
                "id_norma": id_norma,
                "marcador": new.get("marcador", ""),
                "tipo": "fuente_agregada",
                "relevancia": RELEVANCIA_SIGNIFICATIVA,
            })
            continue
        if new is None:
            changes.append({
                "id_norma": id_norma,
                "marcador": old.get("marcador", ""),
                "tipo": "fuente_eliminada",
                "relevancia": RELEVANCIA_SIGNIFICATIVA,
            })
            continue
        status_change = _compare_status(old, new)
        if old.get("huella") == new.get("huella") and status_change is None:
            continue

        details: dict[str, Any] = {}
        if status_change is not None:
            details["estado_norma"] = status_change
        added, removed, updated = _compare_versions(
            old.get("vigencias", []),
            new.get("vigencias", []),
        )
        if added:
            details["vigencias_agregadas"] = added
        if removed:
            details["vigencias_eliminadas"] = removed
        if updated:
            details["vigencias_actualizadas"] = updated
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
        change = {
            "id_norma": id_norma,
            "marcador": new.get("marcador") or old.get("marcador") or "",
            "tipo": "version_actualizada",
            "relevancia": classify_relevance(details, new.get("vigencia_actual"), reference_day),
            "detalles": details,
        }
        if new.get("version_vigente_indicada"):
            change["version_vigente_indicada"] = new["version_vigente_indicada"]
        changes.append(change)
    return changes


def _compare_status(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any] | None:
    """Cambio de estado de la norma, o ``None``.

    Un estado guardado por una versión anterior del motor no trae ``estado``; en ese caso no
    hay con qué comparar y no se informa nada, para no publicar compendios sin cambios reales.
    """
    before = old.get("estado")
    after = new.get("estado")
    if not isinstance(before, dict) or not isinstance(after, dict) or before == after:
        return None
    return {"antes": before, "despues": after}


def status_warnings(change: dict[str, Any]) -> list[str]:
    """Avisos que exigen decidir si la norma sigue en el compendio."""
    status = (change.get("detalles") or {}).get("estado_norma")
    if not status:
        return []
    before = status.get("antes") or {}
    after = status.get("despues") or {}
    warnings: list[str] = []
    before_alerts = {(item.get("clase"), item.get("mensaje")) for item in before.get("alertas", [])}
    for alert in after.get("alertas", []):
        if (alert.get("clase"), alert.get("mensaje")) in before_alerts:
            continue
        message = alert.get("mensaje") or ""
        if alert.get("clase") == "derogado":
            warnings.append(f"Norma derogada según Ley Chile: {message or 'sin detalle'}.")
        elif alert.get("clase") == "refundido":
            warnings.append(f"Norma refundida en otra según Ley Chile: {message or 'sin detalle'}.")
    if after.get("derogado") and not before.get("derogado") and not any(
        alert.get("clase") == "derogado" for alert in after.get("alertas", [])
    ):
        warnings.append("Norma derogada según Ley Chile.")
    if after.get("derogacion_tacita") and after.get("derogacion_tacita") != before.get("derogacion_tacita"):
        detail = json.dumps(after["derogacion_tacita"], ensure_ascii=False)
        warnings.append(f"Ley Chile informa una derogación tácita: {detail}.")
    return warnings


_MONTHS = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}


def _iso_date(value: Any) -> str:
    """Convierte ``16-OCT-2026`` o ``2026-10-16`` a ISO; si no puede, devuelve el texto tal cual."""
    text = _scalar(value)
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    parts = text.split("-")
    if len(parts) == 3 and parts[1].upper() in _MONTHS:
        try:
            return date(int(parts[2]), _MONTHS[parts[1].upper()], int(parts[0])).isoformat()
        except ValueError:
            return text
    return text


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_modifying_norms(payload: Any) -> dict[str, list[dict[str, str]]]:
    """Normas modificatorias por fecha de inicio de la versión que originan.

    ``get_versiones`` cuelga cada modificatoria de la versión que termina, no de la que
    empieza; por eso se agrupan por ``@inicioVigencia`` y nunca por la posición en la lista.
    """
    root = payload.get("Versiones") if isinstance(payload, dict) else None
    if not isinstance(root, dict):
        raise ValueError("Ley Chile respondió sin la lista de versiones.")
    norms: dict[str, list[dict[str, str]]] = {}
    for version in _as_list(root.get("Version")):
        if not isinstance(version, dict):
            continue
        group = version.get("Modificatorias")
        for item in _as_list(group.get("Modificatoria") if isinstance(group, dict) else None):
            if not isinstance(item, dict):
                continue
            start = _iso_date(item.get("@inicioVigencia"))
            norm = {
                "tipo": _scalar(item.get("@tipoNorma")),
                "numero": _scalar(item.get("@nroNorma")),
                "id_norma": _scalar(item.get("@idNorma")),
                "titulo": _scalar(item.get("@titulo")),
                "fecha_publicacion": _iso_date(item.get("@fechaPublicacion")),
                "tipo_modificacion": _scalar(item.get("@tipoModificacion")),
            }
            if start and norm not in norms.setdefault(start, []):
                norms[start].append(norm)
    return norms


def parse_last_modifying_norm(payload: Any, version_start: str) -> dict[str, str] | None:
    """Lee ``get_ultima_modificatoria``; solo vale si declara la misma fecha de inicio."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    if _iso_date(payload.get("fechainiciovigencia")) != version_start:
        return None
    if not _scalar(payload.get("idNorma")):
        return None
    return {
        "tipo": _scalar(payload.get("tipo_norma")),
        "numero": _scalar(payload.get("numero_norma")),
        "id_norma": _scalar(payload.get("idNorma")),
        "titulo": _scalar(payload.get("titulo")),
        "fecha_publicacion": _iso_date(payload.get("fecha_publicacion_origen")),
        "tipo_modificacion": "",
    }


def _get_json(session: requests.Session, url: str, referer: str, attempts: int = 2) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, headers={"Referer": referer}, timeout=60)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(2.0)
    raise RuntimeError(f"No fue posible consultar {url}") from last_error


def fetch_modifying_norms(
    session: requests.Session,
    id_norma: str,
    starts: list[str],
) -> dict[str, Any]:
    """Busca la norma modificatoria de cada versión, sin fallar nunca la revisión.

    Primero en ``get_versiones``, que las trae todas con título. Cuando una ley tiene vigencias
    escalonadas, ese servicio la asocia a una sola fecha; para las demás se consulta
    ``get_ultima_modificatoria``.
    """
    referer = f"https://www.bcn.cl/leychile/navegar?idNorma={id_norma}"
    complete = True
    try:
        by_start = parse_modifying_norms(
            _get_json(session, VERSIONES_URL.format(id_norma=id_norma), referer)
        )
    except Exception:
        by_start = {}
        complete = False
    found: dict[str, list[dict[str, str]]] = {}
    for start in starts:
        norms = list(by_start.get(start, []))
        if not norms and start != FECHA_SIN_DETERMINAR:
            try:
                last = parse_last_modifying_norm(
                    _get_json(session, ULTIMA_MODIFICATORIA_URL.format(id_norma=id_norma, fecha=start), referer),
                    start,
                )
            except Exception:
                last = None
                complete = False
            if last is not None:
                norms.append(last)
        found[start] = norms
    return {"por_vigencia": found, "consulta_completa": complete}


def _modification_starts(change: dict[str, Any], today: date) -> list[str]:
    """Fechas de inicio de las versiones nuevas o vigentes que vale la pena explicar."""
    details = change.get("detalles") or {}
    starts: list[str] = []

    def add(version: Any) -> None:
        start = _scalar(version.get("desde")) if isinstance(version, dict) else ""
        if start and start not in starts:
            starts.append(start)

    for version in details.get("vigencias_agregadas", []):
        add(version)
    if "vigencia_actual" in details:
        add(details["vigencia_actual"].get("despues"))
    for update in details.get("vigencias_actualizadas", []):
        after = update.get("despues") or {}
        end = _parse_date(after.get("hasta"))
        if end is None or end >= today:
            add(after)
    return starts


def attach_modifying_norms(
    changes: list[dict[str, Any]],
    lookup: Callable[[str, list[str]], dict[str, Any]],
    today: date | None = None,
) -> None:
    reference_day = today or datetime.now(timezone.utc).date()
    for change in changes:
        if change.get("tipo") != "version_actualizada":
            continue
        if change.get("relevancia") == RELEVANCIA_INFORMATIVA:
            continue
        starts = _modification_starts(change, reference_day)
        if not starts:
            continue
        try:
            change["normas_modificatorias"] = lookup(str(change["id_norma"]), starts)
        except Exception:
            change["normas_modificatorias"] = {
                "por_vigencia": {start: [] for start in starts},
                "consulta_completa": False,
            }


def _network_modifying_lookup(id_norma: str, starts: list[str]) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(HEADERS | {"Accept": "application/json,text/plain;q=0.9,*/*;q=0.8"})
    try:
        return fetch_modifying_norms(session, id_norma, starts)
    finally:
        session.close()


def review_versions(
    config: dict[str, Any],
    state_path: Path,
    fetcher: Callable[[str, str], dict[str, Any]] | None = None,
    modifying_lookup: Callable[[str, list[str]], dict[str, Any]] | None = None,
) -> ReviewResult:
    """Compara el estado de Ley Chile con el guardado.

    Sin ``fetcher`` se consulta Ley Chile, incluida la búsqueda de normas modificatorias.
    Con un ``fetcher`` inyectado, esa búsqueda solo ocurre si también se inyecta ``modifying_lookup``.
    """
    if fetcher is None and modifying_lookup is None:
        modifying_lookup = _network_modifying_lookup
    current = build_state(config, fetcher)
    if not state_path.exists():
        return ReviewResult(True, False, [], current)
    with state_path.open("r", encoding="utf-8-sig") as handle:
        previous = json.load(handle)
    if previous.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("La versión del esquema de estado no es compatible.")
    previous_sources = previous.get("fuentes") if isinstance(previous.get("fuentes"), dict) else {}
    effective_sources = {key: effective_snapshot(source) for key, source in previous_sources.items()}
    pinned = effective_sources != previous_sources
    previous = dict(previous, fuentes=effective_sources)
    changes = compare_states(previous, current)
    if modifying_lookup is not None:
        attach_modifying_norms(changes, modifying_lookup)
    significant = any(
        change.get("relevancia", RELEVANCIA_SIGNIFICATIVA) != RELEVANCIA_INFORMATIVA
        for change in changes
    )
    migrated = pinned or any(
        isinstance(source, dict) and "estado" not in source
        for source in previous_sources.values()
    )
    return ReviewResult(False, significant, changes, current, migrated)


_CHANGE_LABELS = {
    "version_actualizada": "versión actualizada",
    "fuente_agregada": "fuente agregada",
    "fuente_eliminada": "fuente eliminada",
}

_NORM_TYPES = {
    "LEY": "Ley",
    "DL": "Decreto Ley",
    "DFL": "Decreto con Fuerza de Ley",
    "DTO": "Decreto",
    "DS": "Decreto Supremo",
    "RES": "Resolución",
    "REC": "Rectificación",
}


def _format_number(number: str) -> str:
    if number.isdigit() and len(number) > 3:
        return f"{int(number):,}".replace(",", ".")
    return number


def format_modifying_norm(norm: dict[str, Any]) -> str:
    kind = _scalar(norm.get("tipo"))
    label = _NORM_TYPES.get(kind.upper(), kind.capitalize() or "Norma")
    number = _scalar(norm.get("numero"))
    if number and number.upper() not in ("S/N", "SN"):
        label = f"{label} N.º {_format_number(number)}"
    id_norma = _scalar(norm.get("id_norma"))
    if id_norma.isdigit():
        label = f"[{label}](https://www.bcn.cl/leychile/navegar?idNorma={id_norma})"
    published = _scalar(norm.get("fecha_publicacion"))
    return f"{label}, publicada el {published}" if published else label


def _modifying_lines(change: dict[str, Any], start: str, shown: set[str]) -> list[str]:
    info = change.get("normas_modificatorias")
    if not isinstance(info, dict) or start in shown:
        return []
    by_start = info.get("por_vigencia") or {}
    if start not in by_start:
        return []
    shown.add(start)
    norms = by_start.get(start) or []
    if norms:
        label = "Norma modificatoria" if len(norms) == 1 else "Normas modificatorias"
        return [f"    - {label}: {'; '.join(format_modifying_norm(norm) for norm in norms)}."]
    if info.get("consulta_completa", True):
        return ["    - Ley Chile no informa la norma modificatoria de esta versión."]
    return ["    - No fue posible consultar la norma modificatoria en Ley Chile."]


def _format_change(change: dict[str, Any], pending: list[str] | None = None) -> list[str]:
    marker = change.get("marcador") or "Norma sin título"
    id_norma = change["id_norma"]
    change_type = change["tipo"]
    label = _CHANGE_LABELS.get(change_type, change_type.replace("_", " "))
    warnings = status_warnings(change)
    alert = " ⚠️ requiere revisión" if warnings else ""
    lines = [f"- **{marker}** (`idNorma={id_norma}`): {label}.{alert}"]
    for warning in warnings:
        lines.append(f"  - ⚠️ {warning}")
    if warnings:
        lines.append(
            "  - El compendio todavía la incluye; quitarla o reemplazarla requiere editar "
            "`app/fuentes.json`."
        )
    details = change.get("detalles", {})
    shown: set[str] = set()
    for version in details.get("vigencias_agregadas", []):
        version_label = version.get("descripcion") or version.get("tipo") or "versión"
        lines.append(f"  - Nueva vigencia: {version_label}, {_period(version)}.")
        lines.extend(_modifying_lines(change, _scalar(version.get("desde")), shown))
    for update in details.get("vigencias_actualizadas", []):
        before = update.get("antes") or {}
        after = update.get("despues") or {}
        before_label = before.get("descripcion") or before.get("tipo") or "sin etiqueta"
        after_label = after.get("descripcion") or after.get("tipo") or "sin etiqueta"
        lines.append(
            f"  - Vigencia actualizada/reclasificada: {after_label}, {_period(after)} "
            f"(antes: {before_label})."
        )
        lines.extend(_modifying_lines(change, _scalar(after.get("desde")), shown))
    if "vigencia_actual" in details:
        after = details["vigencia_actual"]["despues"] or {}
        lines.append(
            f"  - Vigencia actual: {after.get('descripcion') or after.get('tipo') or 'sin etiqueta'}, "
            f"{_period(after, open_end=', sin fecha de término')}.")
        if _scalar(after.get("hasta")).startswith(_ANIO_CENTINELA):
            for event in pending or []:
                lines.append(f"    - Pendiente de entrar en vigencia: {event}")
        lines.extend(_modifying_lines(change, _scalar(after.get("desde")), shown))
    if change.get("version_vigente_indicada"):
        lines.append(
            "  - Ley Chile todavía muestra por defecto una versión que ya no está vigente; "
            f"el compendio usa la versión vigente desde {change['version_vigente_indicada']}."
        )
    if "diferido" in details:
        present = bool((details["diferido"]["despues"] or {}).get("presente"))
        lines.append(f"  - Texto diferido: {'presente' if present else 'ya no informado'}.")
    if "eventos_pendientes" in details:
        lines.append("  - Cambiaron los eventos pendientes informados por Ley Chile.")
    return lines


def _period(version: dict[str, Any], open_end: str = "") -> str:
    """Describe el período de una vigencia sin mostrar las fechas centinela de Ley Chile.

    Ley Chile fecha el 2 de febrero de 2222 el inicio de las vigencias que dependen de un
    evento futuro, y el 1 de febrero de 2222 el término de la vigencia que las precede.
    """
    start = _scalar(version.get("desde"))
    end = _scalar(version.get("hasta"))
    if start.startswith(_ANIO_CENTINELA):
        period = "desde una fecha aún no determinada"
    else:
        period = f"desde {start or '?'}"
    if end.startswith(_ANIO_CENTINELA):
        return period + ", sin fecha de término definida (hay una modificación pendiente de entrar en vigencia)"
    if end:
        return period + f" hasta {end}"
    return period + open_end


def format_summary(result: ReviewResult) -> str:
    if result.baseline_created:
        return "Línea base de versiones creada; no se genera un compendio en esta primera revisión.\n"

    significant = result.significant_changes
    informational = result.informational_changes
    if not significant and not informational:
        return "No se detectaron nuevas versiones ni cambios de vigencia o texto diferido.\n"

    blocks: list[str] = []
    if significant:
        lines = ["## Actualizaciones de normas detectadas", ""]
        for change in significant:
            lines.extend(_format_change(change, _pending_events(result.state, change.get("id_norma"))))
        blocks.append("\n".join(lines))
    if informational:
        lines = [
            "## Cambios registrados sin efecto sobre el texto vigente",
            "",
            "Ley Chile ajustó los límites de fecha de vigencias ya históricas. El estado se "
            "actualiza para conservar la trazabilidad, pero no se solicita un nuevo compendio "
            "porque el texto vigente no cambió.",
            "",
        ]
        for change in informational:
            lines.extend(_format_change(change))
        blocks.append("\n".join(lines))
    if significant:
        undated = _undated_current_versions(result.state)
        if undated:
            blocks.append("\n".join(["## Notas sobre versiones", "", *undated]))
    return "\n\n".join(blocks) + "\n"


def _pending_events(state: dict[str, Any], id_norma: Any) -> list[str]:
    """Eventos que Ley Chile informa como pendientes para una norma, en una línea cada uno."""
    sources = state.get("fuentes") if isinstance(state, dict) else None
    source = sources.get(str(id_norma)) if isinstance(sources, dict) else None
    if not isinstance(source, dict):
        return []
    events = source.get("eventos_pendientes") or []
    return [" ".join(_scalar(event).split()) for event in events if _scalar(event)]


def _undated_current_versions(state: dict[str, Any]) -> list[str]:
    """Explica las normas cuya versión por defecto en Ley Chile aún no tiene fecha de vigencia.

    El compendio descarga la versión que muestra la web de Ley Chile. Cuando esa versión
    empieza en la fecha centinela, la web la presenta como «Última Versión» sin fecha y su
    texto puede incluir modificaciones que todavía no rigen.
    """
    sources = state.get("fuentes") if isinstance(state, dict) else None
    if not isinstance(sources, dict):
        return []
    lines: list[str] = []
    for id_norma, source in sources.items():
        if not isinstance(source, dict):
            continue
        current = source.get("vigencia_actual") or {}
        if not _scalar(current.get("desde")).startswith(_ANIO_CENTINELA):
            continue
        dated = [
            version for version in source.get("vigencias") or []
            if isinstance(version, dict)
            and _parse_date(version.get("desde"))
            and not _scalar(version.get("desde")).startswith(_ANIO_CENTINELA)
        ]
        line = (
            f"- **{source.get('marcador') or id_norma}** (`idNorma={id_norma}`): Ley Chile muestra "
            "como «Última Versión» un texto cuya entrada en vigencia aún no tiene fecha determinada, "
            "y el compendio incluye ese texto, igual que la web de Ley Chile. Puede contener "
            "modificaciones que todavía no rigen"
        )
        if dated:
            last = max(dated, key=lambda version: _scalar(version.get("desde")))
            line += f"; la última versión con fecha de inicio es la del {_scalar(last.get('desde'))}"
        lines.append(line + ".")
    return lines


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_failure_output(error: BaseException) -> None:
    """Informa a los workflows si la falla fue transitoria, para no abrir un issue por ella."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    kind = error.tipo if isinstance(error, RevisionFallida) else "error"
    detail = " ".join(str(error).split()) or type(error).__name__
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"tipo_falla={kind}\n")
        handle.write(f"detalle_falla={detail}\n")


def write_github_output(result: ReviewResult) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    flags = {
        "changes_detected": result.changes_detected,
        "baseline_created": result.baseline_created,
        "informational_changes": bool(result.informational_changes),
        "state_updated": result.state_updated,
        "avisos_norma": bool(result.status_warning_changes),
    }
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for name, value in flags.items():
            handle.write(f"{name}={'true' if value else 'false'}\n")
