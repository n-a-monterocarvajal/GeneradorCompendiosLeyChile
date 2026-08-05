from __future__ import annotations

import argparse
import traceback
from pathlib import Path

from .config import read_config
from .generador import CompendiumGenerator
from .versiones import format_summary, review_versions, write_github_output, write_json_atomic


def generar_main() -> int:
    parser = argparse.ArgumentParser(description="Genera un compendio PDF de normas de Ley Chile.")
    parser.add_argument("--config", required=True, help="Ruta a fuentes.json")
    parser.add_argument("--salida", help="Ruta opcional del PDF final")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = read_config(config_path)
    output_path = Path(args.salida).resolve() if args.salida else None
    generator = CompendiumGenerator(config_path.parent.parent, config, output_path)
    try:
        generator.run()
        return 0
    except Exception:
        generator.log("\nERROR:")
        generator.log(traceback.format_exc())
        raise


def revisar_main() -> int:
    parser = argparse.ArgumentParser(description="Revisa nuevas versiones de las normas configuradas.")
    parser.add_argument("--config", required=True, help="Ruta a fuentes.json")
    parser.add_argument("--estado", required=True, help="Estado persistente de versiones")
    parser.add_argument("--cambios", required=True, help="Salida JSON con cambios")
    parser.add_argument("--resumen", required=True, help="Salida Markdown para notas de release")
    args = parser.parse_args()

    config = read_config(Path(args.config))
    state_path = Path(args.estado)
    result = review_versions(config, state_path)
    if result.baseline_created or result.changes_detected:
        write_json_atomic(state_path, result.state)
    write_json_atomic(Path(args.cambios), {"cambios": result.changes})
    summary = format_summary(result)
    summary_path = Path(args.resumen)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary, encoding="utf-8")
    write_github_output(result.changes_detected, result.baseline_created)
    print(summary, end="")
    return 0
