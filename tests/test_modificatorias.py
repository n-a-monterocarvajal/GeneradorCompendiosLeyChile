import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import requests

from generador_compendios_leychile.generador import pin_export_version
from generador_compendios_leychile.versiones import (
    ReviewResult,
    attach_modifying_norms,
    compare_states,
    fetch_modifying_norms,
    format_modifying_norm,
    format_summary,
    indicated_current_version,
    normalize_snapshot,
    parse_last_modifying_norm,
    parse_modifying_norms,
    review_versions,
    write_github_output,
)


def norma(*, versions, current_start, derogado=False, alertas=None, alerta_global=""):
    return {
        "metadatos": {
            "titulo_norma": "DETERMINA CONDUCTAS TERRORISTAS Y FIJA SU PENALIDAD",
            "tipo_version": "2",
            "tipo_version_s": "Última Versión",
            "vigencia": {"inicio_vigencia": current_start, "fin_vigencia": ""},
            "vigencias": versions,
            "fecha_version": current_start,
            "fecha_actualizacion_texto": "1984-05-17",
            "eventos_pendientes": [],
            "derogado": derogado,
            "derogacion_tacita": [],
        },
        "alertas": alertas or [],
        "alerta_global": alerta_global,
    }


ANTES = [
    {"desde": "2015-10-22", "hasta": "2024-09-03", "tipo_version": "1", "tipo_version_s": "Intermedio"},
    {"desde": "2024-09-04", "hasta": "", "tipo_version": "2", "tipo_version_s": "Última Versión"},
]
DESPUES = [
    ANTES[0],
    {"desde": "2024-09-04", "hasta": "2025-02-11", "tipo_version": "1", "tipo_version_s": "Intermedio"},
    {"desde": "2025-02-12", "hasta": "", "tipo_version": "2", "tipo_version_s": "Última Versión"},
]
DEROGADA = [{"clase": "derogado", "texto": "Derogado", "mensaje": "Esta norma ha sido derogada el 12-FEB-2025"}]

# Forma de get_versiones: la modificatoria cuelga de la versión que termina.
VERSIONES = {
    "Versiones": {
        "@id_norma": 29731,
        "Version": [
            {"@tipoVersion": "Última Versión", "@vigenteDesde": "2025-02-12"},
            {
                "@vigenteHasta": "2025-02-11", "@tipoVersion": "Intermedio", "@vigenteDesde": "2024-09-04",
                "Modificatorias": {"Modificatoria": {
                    "@tipoModificacion": "MODIFICACION", "@tipoNorma": "LEY", "@idNorma": 1211036,
                    "@nroNorma": 21732, "@titulo": "DETERMINA CONDUCTAS TERRORISTAS, FIJA SU PENALIDAD Y DEROGA LA LEY Nº 18.314",
                    "@fechaPublicacion": "12-FEB-2025", "@inicioVigencia": "2025-02-12",
                }},
            },
            {
                "@vigenteHasta": "2024-09-03", "@tipoVersion": "Intermedio", "@vigenteDesde": "2015-10-22",
                "Modificatorias": {"Modificatoria": [
                    {"@tipoNorma": "LEY", "@idNorma": 1, "@nroNorma": 21555, "@fechaPublicacion": "10-ABR-2023",
                     "@inicioVigencia": "2024-09-04"},
                    {"@tipoNorma": "REC", "@idNorma": 2, "@nroNorma": "S/N", "@fechaPublicacion": "10-ABR-2023",
                     "@inicioVigencia": "2024-09-04"},
                ]},
            },
        ],
    }
}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        for fragment, answer in self.routes.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return FakeResponse(answer)
        raise AssertionError(f"URL inesperada: {url}")


def snapshots(old_payload, new_payload):
    return (
        {"fuentes": {"29731": normalize_snapshot("29731", "Ley N.º 18.314.", old_payload)}},
        {"fuentes": {"29731": normalize_snapshot("29731", "Ley N.º 18.314.", new_payload)}},
    )


class ModifyingNormTests(unittest.TestCase):
    def test_groups_by_start_of_the_version_they_create(self):
        norms = parse_modifying_norms(VERSIONES)
        self.assertEqual(["21732"], [item["numero"] for item in norms["2025-02-12"]])
        self.assertEqual("2025-02-12", norms["2025-02-12"][0]["fecha_publicacion"])
        self.assertEqual(["21555", "S/N"], [item["numero"] for item in norms["2024-09-04"]])

    def test_without_version_list_is_an_error(self):
        with self.assertRaises(ValueError):
            parse_modifying_norms({"html": []})

    def test_last_modifying_norm_must_match_the_version(self):
        body = json.dumps({
            "fechainiciovigencia": "2025-01-24", "fecha_publicacion_origen": "2024-10-24",
            "idNorma": 1207746, "numero_norma": "21713", "titulo": "", "tipo_norma": "Ley",
        })
        self.assertEqual("21713", parse_last_modifying_norm(body, "2025-01-24")["numero"])
        self.assertIsNone(parse_last_modifying_norm(body, "2025-03-07"))
        self.assertIsNone(parse_last_modifying_norm("{}", "2025-11-28"))

    def test_staggered_versions_fall_back_to_last_modifying_norm(self):
        session = FakeSession({
            "get_versiones": {"Versiones": {"Version": [{"@vigenteDesde": "2025-01-24"}]}},
            "idVersion=2025-01-24": json.dumps({
                "fechainiciovigencia": "2025-01-24", "fecha_publicacion_origen": "2024-10-24",
                "idNorma": 1207746, "numero_norma": "21713", "tipo_norma": "Ley",
            }),
            "idVersion=2025-11-28": "{}",
        })
        result = fetch_modifying_norms(session, "6374", ["2025-01-24", "2025-11-28"])
        self.assertTrue(result["consulta_completa"])
        self.assertEqual("21713", result["por_vigencia"]["2025-01-24"][0]["numero"])
        self.assertEqual([], result["por_vigencia"]["2025-11-28"])

    def test_failed_lookup_never_fails_the_review(self):
        session = FakeSession({"servicios": requests.ConnectionError("sin red")})
        with mock.patch("generador_compendios_leychile.versiones.time.sleep"):
            result = fetch_modifying_norms(session, "29731", ["2025-02-12"])
        self.assertFalse(result["consulta_completa"])
        self.assertEqual({"2025-02-12": []}, result["por_vigencia"])

    def test_format(self):
        self.assertEqual(
            "[Ley N.º 21.849](https://www.bcn.cl/leychile/navegar?idNorma=1229167), publicada el 2026-10-16",
            format_modifying_norm({"tipo": "LEY", "numero": "21849", "id_norma": "1229167",
                                   "fecha_publicacion": "2026-10-16"}),
        )
        self.assertTrue(format_modifying_norm({"tipo": "REC", "numero": "S/N", "id_norma": "2"})
                        .startswith("[Rectificación]"))

    def test_summary_names_the_modifying_law_of_each_new_version(self):
        old, new = snapshots(
            norma(versions=ANTES, current_start="2024-09-04"),
            norma(versions=DESPUES, current_start="2025-02-12"),
        )
        changes = compare_states(old, new, date(2026, 10, 16))
        session = FakeSession({"get_versiones": VERSIONES})
        attach_modifying_norms(
            changes, lambda id_norma, starts: fetch_modifying_norms(session, id_norma, starts), date(2026, 10, 16)
        )
        summary = format_summary(ReviewResult(False, True, changes, {}))
        self.assertIn("Norma modificatoria: [Ley N.º 21.732]", summary)
        self.assertEqual(1, summary.count("Ley N.º 21.732"), "una vez por versión, aunque se mencione dos veces")
        self.assertNotIn("2015-10-22", json.dumps(changes[0]["normas_modificatorias"]))

    def test_summary_says_when_ley_chile_does_not_inform_it(self):
        old, new = snapshots(
            norma(versions=ANTES, current_start="2024-09-04"),
            norma(versions=DESPUES, current_start="2025-02-12"),
        )
        changes = compare_states(old, new, date(2026, 10, 16))
        attach_modifying_norms(
            changes,
            lambda _id, starts: {"por_vigencia": {start: [] for start in starts}, "consulta_completa": True},
            date(2026, 10, 16),
        )
        summary = format_summary(ReviewResult(False, True, changes, {}))
        self.assertIn("Ley Chile no informa la norma modificatoria", summary)

    def test_injected_fetcher_skips_network_lookup(self):
        config = {"fuentes": [{"id_norma": "29731", "marcador": "Ley N.º 18.314."}]}
        old, new = snapshots(
            norma(versions=ANTES, current_start="2024-09-04"),
            norma(versions=DESPUES, current_start="2025-02-12"),
        )
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "estado.json"
            state_path.write_text(json.dumps({"schema_version": 1, **old}), encoding="utf-8")
            result = review_versions(config, state_path, lambda *_: new["fuentes"]["29731"])
        self.assertTrue(result.changes_detected)
        self.assertNotIn("normas_modificatorias", result.changes[0])


class StatusTests(unittest.TestCase):
    def test_status_is_outside_the_fingerprint(self):
        plain = norma(versions=ANTES, current_start="2024-09-04")
        flagged = norma(versions=ANTES, current_start="2024-09-04", derogado=True, alertas=DEROGADA)
        self.assertEqual(
            normalize_snapshot("29731", "x", plain)["huella"],
            normalize_snapshot("29731", "x", flagged)["huella"],
        )

    def test_derogation_is_reported_and_asks_for_review(self):
        old, new = snapshots(
            norma(versions=ANTES, current_start="2024-09-04"),
            norma(versions=DESPUES, current_start="2025-02-12", derogado=True, alertas=DEROGADA),
        )
        changes = compare_states(old, new, date(2026, 10, 16))
        result = ReviewResult(False, True, changes, {})
        self.assertEqual(["29731"], [item["id_norma"] for item in result.status_warning_changes])
        summary = format_summary(result)
        self.assertIn("⚠️ Norma derogada según Ley Chile: Esta norma ha sido derogada el 12-FEB-2025.", summary)
        self.assertIn("`app/fuentes.json`", summary)

    def test_derogation_alone_is_significant(self):
        old, new = snapshots(
            norma(versions=ANTES, current_start="2024-09-04"),
            norma(versions=ANTES, current_start="2024-09-04", derogado=True, alertas=DEROGADA),
        )
        changes = compare_states(old, new, date(2026, 10, 16))
        self.assertEqual("significativa", changes[0]["relevancia"])
        self.assertIn("estado_norma", changes[0]["detalles"])

    def test_refundition_and_tacit_derogation_are_warnings(self):
        refundida = [{"clase": "refundido", "mensaje": "Esta norma ha sido refundida por DFL-1 29-OCT-2009"}]
        tacita = norma(versions=ANTES, current_start="2024-09-04", alertas=refundida)
        tacita["metadatos"]["derogacion_tacita"] = ["Ley 21.000"]
        old, new = snapshots(norma(versions=ANTES, current_start="2024-09-04"), tacita)
        summary = format_summary(ReviewResult(False, True, compare_states(old, new), {}))
        self.assertIn("Norma refundida en otra según Ley Chile", summary)
        self.assertIn("derogación tácita", summary)

    def test_state_from_previous_engine_does_not_trigger_and_is_migrated(self):
        config = {"fuentes": [{"id_norma": "29731", "marcador": "Ley N.º 18.314."}]}
        snapshot = normalize_snapshot("29731", "Ley N.º 18.314.", norma(
            versions=DESPUES, current_start="2025-02-12", derogado=True, alertas=DEROGADA,
        ))
        legacy = {key: value for key, value in snapshot.items() if key not in ("estado", "version_vigente_indicada")}
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "estado.json"
            state_path.write_text(json.dumps({"schema_version": 1, "fuentes": {"29731": legacy}}), encoding="utf-8")
            result = review_versions(config, state_path, lambda *_: snapshot)
        self.assertEqual([], result.changes)
        self.assertFalse(result.changes_detected)
        self.assertTrue(result.state_updated, "el estado nuevo debe guardarse para comparar desde mañana")

    def test_output_flags_status_warnings(self):
        old, new = snapshots(
            norma(versions=ANTES, current_start="2024-09-04"),
            norma(versions=ANTES, current_start="2024-09-04", derogado=True, alertas=DEROGADA),
        )
        result = ReviewResult(False, True, compare_states(old, new), {})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "salida"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                write_github_output(result)
            self.assertIn("avisos_norma=true", output.read_text(encoding="utf-8"))


class CurrentVersionTests(unittest.TestCase):
    def test_indicated_current_version(self):
        alert = {"m": ["El texto de esta versión no se encuentra vigente"],
                 "e": {"i": "1007469", "p": "", "f": "2026-10-16", "t": "Ir al texto vigente"}}
        self.assertEqual("2026-10-16", indicated_current_version("1007469", alert))
        self.assertEqual("", indicated_current_version("176595", alert))
        self.assertEqual("", indicated_current_version("29731", {"m": ["Derogada"], "e": {"i": "", "f": ""}}))
        self.assertEqual("", indicated_current_version("29731", ""))

    def test_summary_explains_the_pinned_version(self):
        old, new = snapshots(
            norma(versions=ANTES, current_start="2024-09-04"),
            norma(versions=DESPUES, current_start="2024-09-04",
                  alerta_global={"e": {"i": "", "f": "2025-02-12"}}),
        )
        summary = format_summary(ReviewResult(False, True, compare_states(old, new), {}))
        self.assertIn("el compendio usa la versión vigente desde 2025-02-12", summary)

    def test_pin_export_version(self):
        href = ("https://nuevo.leychile.cl/servicios/Consulta/Exportar?radioExportar=Normas"
                "&exportar_formato=pdf&hddResultadoExportar=1007469.2026-08-27.0.0%23")
        pinned = pin_export_version(href, "2026-10-16")
        self.assertTrue(pinned.endswith("hddResultadoExportar=1007469.2026-10-16.0.0%23"))
        self.assertEqual(href, pin_export_version(href, None))
        self.assertEqual("https://otro", pin_export_version("https://otro", "2026-10-16"))


if __name__ == "__main__":
    unittest.main()
