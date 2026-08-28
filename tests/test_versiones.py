import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from generador_compendios_leychile.versiones import (
    ReviewResult,
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
        summary = format_summary(ReviewResult(False, True, changes, {}))
        self.assertIn("Código Penal", summary)
        self.assertIn("Texto diferido", summary)


    def test_historical_reclassification_does_not_trigger_generation(self):
        """Caso Decreto N.º 55 de 1977: BCN corrige el corte entre dos versiones de 1991."""
        vigencia_actual = {
            "desde": "2023-02-08", "hasta": "", "tipo_version": "2",
            "tipo_version_s": "Última Versión",
        }
        old_versions = [
            {
                "desde": "1980-03-08", "hasta": "1991-08-06", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "1991-08-07", "hasta": "2023-02-07", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            vigencia_actual,
        ]
        new_versions = [
            {
                "desde": "1980-03-08", "hasta": "1991-12-31", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "1992-01-01", "hasta": "2023-02-07", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            vigencia_actual,
        ]
        old_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=old_versions, current_start="2023-02-08")
        )
        new_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=new_versions, current_start="2023-02-08")
        )

        changes = compare_states(
            {"fuentes": {"8355": old_source}},
            {"fuentes": {"8355": new_source}},
            date(2026, 8, 27),
        )

        self.assertEqual(1, len(changes))
        self.assertEqual("informativa", changes[0]["relevancia"])
        details = changes[0]["detalles"]
        self.assertNotIn("vigencia_actual", details)
        self.assertNotIn("vigencias_agregadas", details)
        self.assertNotIn("vigencias_eliminadas", details)
        self.assertEqual(
            [["hasta"], ["desde"]],
            [item["campos_modificados"] for item in details["vigencias_actualizadas"]],
            "el corrimiento del límite entre dos tramos contiguos es una actualización",
        )

    def test_pure_historical_shift_is_informational_and_persists_state(self):
        old_versions = [
            {
                "desde": "1980-03-08", "hasta": "1991-08-06", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "2023-02-08", "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
        ]
        new_versions = [
            {
                "desde": "1980-03-08", "hasta": "1991-12-31", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            old_versions[1],
        ]
        old_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=old_versions, current_start="2023-02-08")
        )
        new_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=new_versions, current_start="2023-02-08")
        )

        config = {"fuentes": [{"id_norma": "8355", "marcador": "Reglamento IVA"}]}
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "schema_version": 1, "actualizado_utc": "antes",
                "fuentes": {"8355": old_source},
            }), encoding="utf-8")
            result = review_versions(config, state_path, lambda *_: new_source)

        self.assertFalse(result.changes_detected)
        self.assertTrue(result.state_updated)
        self.assertEqual([], result.significant_changes)
        self.assertEqual(1, len(result.informational_changes))
        self.assertEqual(
            new_source, result.state["fuentes"]["8355"],
            "el estado debe conservar la reclasificación para no volver a reportarla",
        )

        summary = format_summary(result)
        self.assertIn("sin efecto sobre el texto vigente", summary)
        self.assertNotIn("## Actualizaciones de normas detectadas", summary)
        self.assertIn("Reglamento IVA", summary)

    def test_reclassification_of_current_period_still_triggers(self):
        old_versions = [
            {
                "desde": "1980-03-08", "hasta": "2023-02-07", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "2023-02-08", "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
        ]
        new_versions = [
            old_versions[0],
            {
                "desde": "2023-02-08", "hasta": "2026-12-31", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
        ]
        old_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=old_versions, current_start="2023-02-08")
        )
        new_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=new_versions, current_start="2023-02-08")
        )

        changes = compare_states(
            {"fuentes": {"8355": old_source}},
            {"fuentes": {"8355": new_source}},
            date(2026, 8, 27),
        )
        self.assertEqual("significativa", changes[0]["relevancia"])

    def test_historical_period_extended_into_the_present_triggers(self):
        old_versions = [
            {
                "desde": "1980-03-08", "hasta": "1991-08-06", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "2023-02-08", "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
        ]
        new_versions = [
            {
                "desde": "1980-03-08", "hasta": "2027-01-01", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            old_versions[1],
        ]
        old_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=old_versions, current_start="2023-02-08")
        )
        new_source = normalize_snapshot(
            "8355", "Reglamento IVA", payload(versions=new_versions, current_start="2023-02-08")
        )

        changes = compare_states(
            {"fuentes": {"8355": old_source}},
            {"fuentes": {"8355": new_source}},
            date(2026, 8, 27),
        )
        self.assertEqual("significativa", changes[0]["relevancia"])

    def test_deferred_alert_with_historical_shift_still_triggers(self):
        old_versions = [
            {
                "desde": "1980-03-08", "hasta": "1991-08-06", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "2023-02-08", "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
        ]
        new_versions = [
            {
                "desde": "1980-03-08", "hasta": "1991-12-31", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            old_versions[1],
        ]
        old_source = normalize_snapshot(
            "8355", "Reglamento IVA",
            payload(versions=old_versions, current_start="2023-02-08"),
        )
        new_source = normalize_snapshot(
            "8355", "Reglamento IVA",
            payload(versions=new_versions, current_start="2023-02-08", deferred=True),
        )

        changes = compare_states(
            {"fuentes": {"8355": old_source}},
            {"fuentes": {"8355": new_source}},
            date(2026, 8, 27),
        )
        self.assertEqual("significativa", changes[0]["relevancia"])

    def test_summary_separates_significant_and_informational_norms(self):
        historical_old = [
            {
                "desde": "1980-03-08", "hasta": "1991-08-06", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            {
                "desde": "2023-02-08", "hasta": "", "tipo_version": "2",
                "tipo_version_s": "Última Versión",
            },
        ]
        historical_new = [
            {
                "desde": "1980-03-08", "hasta": "1991-12-31", "tipo_version": "1",
                "tipo_version_s": "Intermedio",
            },
            historical_old[1],
        ]
        old_sources = {
            "8355": normalize_snapshot(
                "8355", "Reglamento IVA",
                payload(versions=historical_old, current_start="2023-02-08"),
            ),
            "1984": normalize_snapshot("1984", "Código Penal", payload()),
        }
        new_sources = {
            "8355": normalize_snapshot(
                "8355", "Reglamento IVA",
                payload(versions=historical_new, current_start="2023-02-08"),
            ),
            "1984": normalize_snapshot("1984", "Código Penal", payload(deferred=True)),
        }

        changes = compare_states(
            {"fuentes": old_sources}, {"fuentes": new_sources}, date(2026, 8, 27)
        )
        result = ReviewResult(False, True, changes, {})

        self.assertEqual(["1984"], [item["id_norma"] for item in result.significant_changes])
        self.assertEqual(["8355"], [item["id_norma"] for item in result.informational_changes])
        summary = format_summary(result)
        self.assertIn("## Actualizaciones de normas detectadas", summary)
        self.assertIn("## Cambios registrados sin efecto sobre el texto vigente", summary)
        self.assertLess(
            summary.index("## Actualizaciones"),
            summary.index("## Cambios registrados"),
        )

    def test_added_source_is_significant(self):
        snapshot = normalize_snapshot("1984", "Código Penal", payload())
        changes = compare_states({"fuentes": {}}, {"fuentes": {"1984": snapshot}})
        self.assertEqual("significativa", changes[0]["relevancia"])

if __name__ == "__main__":
    unittest.main()
