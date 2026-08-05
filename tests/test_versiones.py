import json
import tempfile
import unittest
from pathlib import Path

from generador_compendios_leychile.versiones import (
    compare_states,
    format_summary,
    normalize_snapshot,
    review_versions,
)


def payload(*, versions=None, current_start="2025-01-01", deferred=False):
    return {
        "metadatos": {
            "titulo_norma": "CÓDIGO PENAL",
            "tipo_version": "2",
            "tipo_version_s": "Última Versión",
            "vigencia": {"inicio_vigencia": current_start, "fin_vigencia": ""},
            "vigencias": versions or [{
                "desde": current_start, "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            }],
            "fecha_version": "1874-11-12",
            "fecha_actualizacion_texto": "1874-11-12",
            "eventos_pendientes": [],
        },
        "alertas": ({
            "clase": "diferido", "texto": "Tiene texto diferido",
            "mensaje": "Rige más adelante",
        } if deferred else []),
    }


class VersionTests(unittest.TestCase):
    def test_snapshot_is_order_independent(self):
        versions = [
            {"desde": "2025-01-01", "hasta": "", "tipo_version": "2", "tipo_version_s": "Última"},
            {"desde": "2024-01-01", "hasta": "2024-12-31", "tipo_version": "1", "tipo_version_s": "Intermedio"},
        ]
        first = normalize_snapshot("1984", "Código Penal", payload(versions=versions))
        second = normalize_snapshot("1984", "Código Penal", payload(versions=list(reversed(versions))))
        self.assertEqual(first["huella"], second["huella"])

    def test_added_version_is_reported(self):
        old_source = normalize_snapshot("1984", "Código Penal", payload())
        new_versions = payload()["metadatos"]["vigencias"] + [{
            "desde": "2026-08-12", "hasta": "", "tipo_version": "7",
            "tipo_version_s": "Con Vigencia Diferida por Fecha",
        }]
        new_source = normalize_snapshot("1984", "Código Penal", payload(versions=new_versions))
        changes = compare_states(
            {"fuentes": {"1984": old_source}},
            {"fuentes": {"1984": new_source}},
        )
        self.assertEqual(1, len(changes))
        self.assertEqual("2026-08-12", changes[0]["detalles"]["vigencias_agregadas"][0]["desde"])

    def test_deferred_change_is_reported(self):
        old_source = normalize_snapshot("1984", "Código Penal", payload(deferred=False))
        new_source = normalize_snapshot("1984", "Código Penal", payload(deferred=True))
        changes = compare_states(
            {"fuentes": {"1984": old_source}},
            {"fuentes": {"1984": new_source}},
        )
        self.assertIn("diferido", changes[0]["detalles"])

    def test_first_review_creates_baseline_without_change(self):
        config = {"fuentes": [{"id_norma": "1984", "marcador": "Código Penal"}]}
        snapshot = normalize_snapshot("1984", "Código Penal", payload())
        with tempfile.TemporaryDirectory() as directory:
            result = review_versions(config, Path(directory) / "state.json", lambda *_: snapshot)
        self.assertTrue(result.baseline_created)
        self.assertFalse(result.changes_detected)

    def test_unchanged_review_does_not_trigger(self):
        config = {"fuentes": [{"id_norma": "1984", "marcador": "Código Penal"}]}
        snapshot = normalize_snapshot("1984", "Código Penal", payload())
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "schema_version": 1, "actualizado_utc": "antes", "fuentes": {"1984": snapshot}
            }), encoding="utf-8")
            result = review_versions(config, state_path, lambda *_: snapshot)
        self.assertFalse(result.baseline_created)
        self.assertFalse(result.changes_detected)

    def test_summary_names_updated_norm(self):
        old_source = normalize_snapshot("1984", "Código Penal", payload())
        new_source = normalize_snapshot("1984", "Código Penal", payload(deferred=True))
        changes = compare_states(
            {"fuentes": {"1984": old_source}},
            {"fuentes": {"1984": new_source}},
        )
        from generador_compendios_leychile.versiones import ReviewResult
        summary = format_summary(ReviewResult(False, True, changes, {}))
        self.assertIn("Código Penal", summary)
        self.assertIn("Texto diferido", summary)


if __name__ == "__main__":
    unittest.main()
