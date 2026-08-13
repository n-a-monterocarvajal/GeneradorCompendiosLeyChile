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

    def test_august_12_reclassifications_are_updates_not_new_versions(self):
        old_versions = [
            {
                "desde": "2025-01-01", "hasta": "2026-08-11", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
            {
                "desde": "2026-08-12", "hasta": "", "tipo_version": "7",
                "tipo_version_s": "Con Vigencia Diferida por Fecha",
            },
        ]
        new_versions = [
            {
                "desde": "2025-01-01", "hasta": "2026-08-11", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "2026-08-12", "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
        ]
        old_source = normalize_snapshot("1984", "Código Penal", payload(versions=old_versions))
        new_source = normalize_snapshot("1984", "Código Penal", payload(versions=new_versions))

        changes = compare_states(
            {"fuentes": {"1984": old_source}},
            {"fuentes": {"1984": new_source}},
        )

        self.assertEqual("version_actualizada", changes[0]["tipo"])
        details = changes[0]["detalles"]
        self.assertNotIn("vigencias_agregadas", details)
        self.assertNotIn("vigencias_eliminadas", details)
        self.assertEqual(2, len(details["vigencias_actualizadas"]))
        self.assertEqual(
            ["Última Versión", "Con Vigencia Diferida por Fecha"],
            [item["antes"]["descripcion"] for item in details["vigencias_actualizadas"]],
        )
        self.assertEqual(
            ["Intermedio", "Última Versión"],
            [item["despues"]["descripcion"] for item in details["vigencias_actualizadas"]],
        )

        from generador_compendios_leychile.versiones import ReviewResult
        summary = format_summary(ReviewResult(False, True, changes, {}))
        self.assertEqual(2, summary.count("Vigencia actualizada/reclasificada"))
        self.assertNotIn("Nueva vigencia", summary)

        serialized = json.loads(json.dumps({"cambios": changes}, ensure_ascii=False))
        self.assertEqual("version_actualizada", serialized["cambios"][0]["tipo"])
        self.assertIn("vigencias_actualizadas", serialized["cambios"][0]["detalles"])

    def test_same_start_collisions_preserve_exact_matches(self):
        old_versions = [
            {
                "desde": "2026-08-12", "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
            {
                "desde": "2026-08-12", "hasta": "2026-08-12", "tipo_version": "7",
                "tipo_version_s": "Diferida",
            },
        ]
        new_versions = [
            old_versions[0],
            {
                "desde": "2026-08-12", "hasta": "2026-08-13", "tipo_version": "7",
                "tipo_version_s": "Diferida actualizada",
            },
        ]
        old_source = normalize_snapshot("1984", "Código Penal", payload(versions=old_versions))
        new_source = normalize_snapshot("1984", "Código Penal", payload(versions=new_versions))

        details = compare_states(
            {"fuentes": {"1984": old_source}},
            {"fuentes": {"1984": new_source}},
        )[0]["detalles"]

        self.assertEqual(1, len(details["vigencias_actualizadas"]))
        update = details["vigencias_actualizadas"][0]
        self.assertEqual("Diferida", update["antes"]["descripcion"])
        self.assertEqual(["hasta", "descripcion"], update["campos_modificados"])

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
