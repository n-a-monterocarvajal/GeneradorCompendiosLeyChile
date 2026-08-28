# GeneradorCompendiosLeyChile

Motor reutilizable para descargar normas desde Ley Chile/BCN, unirlas en un PDF con marcadores y detectar nuevas versiones de las normas configuradas.

Los repositorios de compendios particulares conservan únicamente su `fuentes.json`, sus workflows y una referencia fijada a una versión de este paquete. Las correcciones del obtenedor y del monitor de versiones se desarrollan y prueban aquí.

## Instalación

```bash
python -m pip install .
python -m playwright install --with-deps chromium
```

## Generar un compendio

```bash
generar-compendio-leychile --config app/fuentes.json
```

La configuración mantiene el contrato de los repositorios existentes:

```json
{
  "titulo_compendio": "Compendio de ejemplo",
  "salida_base": "CompendioEjemplo",
  "bcn_navegador_visible": false,
  "fuentes": [
    {
      "tipo": "bcn",
      "id_norma": "1984",
      "url": "https://www.bcn.cl/leychile/navegar?idNorma=1984",
      "archivo": "codigo_penal",
      "marcador": "Código Penal"
    }
  ]
}
```

Opcionalmente se puede agregar:

```json
{
  "descarga": {
    "reintentos_por_norma": 3,
    "reiniciar_contexto_cada": 6,
    "espera_base_segundos": 4
  }
}
```

## Revisar versiones

```bash
revisar-versiones-leychile \
  --config app/fuentes.json \
  --estado .github/estado-versiones.json \
  --cambios trabajo/cambios-versiones.json \
  --resumen trabajo/cambios-versiones.md
```

El estado normaliza las vigencias informadas por Ley Chile, la vigencia actual, eventos pendientes y alertas de texto diferido. La primera ejecución crea una línea base sin solicitar un compendio. Ejecuciones posteriores reportan solo diferencias semánticas; cambios de orden o valores volátiles no disparan una publicación.

Los cambios se clasifican además por relevancia. Cuando Ley Chile sólo corrige fechas de vigencias ya históricas —algo que no altera el texto que se descarga hoy— el cambio se registra en el estado y en el resumen, pero no solicita un compendio nuevo. En `$GITHUB_OUTPUT` se publican cuatro señales:

| Salida | Significado |
| --- | --- |
| `changes_detected` | Hay cambios que justifican regenerar y publicar. |
| `informational_changes` | Hay cambios registrados sin efecto sobre el texto vigente. |
| `state_updated` | El archivo de estado cambió y debe versionarse. |
| `baseline_created` | Se creó la línea base en esta primera revisión. |

## Desarrollo

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Las decisiones y resguardos se documentan en [docs/decisiones-tecnicas.md](docs/decisiones-tecnicas.md). El [análisis de `ngosang/trackerslist`](docs/analisis-trackerslist.md) registra los patrones de automatización evaluados, las alternativas descartadas y la decisión de publicar artefactos verificables e inmutables. El [plan de migración a repositorio privado](docs/plan-privatizacion.md) describe la autenticación, los controles contra filtraciones y el despliegue coordinado previstos. El código publicado actualmente está bajo licencia MIT.
