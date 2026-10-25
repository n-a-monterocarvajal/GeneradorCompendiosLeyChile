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

Cada norma se descarga en la versión vigente que informa el servicio JSON de Ley Chile, que es la misma que compara la revisión de versiones. La página de una norma puede seguir ofreciendo por unas horas una versión que ya no está vigente; en ese caso el generador cambia la fecha del enlace de exportación y lo deja anotado en el log. Si esa consulta falla, descarga la versión que ofrece la página.

## Revisar versiones

```bash
revisar-versiones-leychile \
  --config app/fuentes.json \
  --estado .github/estado-versiones.json \
  --cambios trabajo/cambios-versiones.json \
  --resumen trabajo/cambios-versiones.md
```

El estado normaliza las vigencias informadas por Ley Chile, la vigencia actual, eventos pendientes y alertas de texto diferido. La primera ejecución crea una línea base sin solicitar un compendio. Ejecuciones posteriores reportan solo diferencias semánticas; cambios de orden o valores volátiles no disparan una publicación.

Los cambios se clasifican además por relevancia. Cuando Ley Chile sólo corrige fechas de vigencias ya históricas —algo que no altera el texto que se descarga hoy— el cambio se registra en el estado y en el resumen, pero no solicita un compendio nuevo.

El resumen indica, bajo cada vigencia nueva, la norma que la originó, tomada del servicio de versiones de Ley Chile o, cuando una ley tiene vigencias escalonadas, de su servicio de última modificatoria. Si Ley Chile no la informa, o no se pudo consultar, el resumen lo dice; esa consulta nunca hace fallar la revisión.

El estado guarda además, fuera de la huella, si la norma está derogada expresa o tácitamente o fue refundida en otra. Cuando eso cambia, el resumen lo destaca al comienzo de la norma y la revisión publica `avisos_norma`, porque alguien debe decidir si la norma sigue en el compendio. Un estado guardado por una versión anterior del motor se completa sin informar cambios.

En `$GITHUB_OUTPUT` se publican cinco señales:

| Salida | Significado |
| --- | --- |
| `changes_detected` | Hay cambios que justifican regenerar y publicar. |
| `informational_changes` | Hay cambios registrados sin efecto sobre el texto vigente. |
| `state_updated` | El archivo de estado cambió y debe versionarse. |
| `baseline_created` | Se creó la línea base en esta primera revisión. |
| `avisos_norma` | Una norma pasó a derogada o refundida según Ley Chile y requiere decidir si sigue en el compendio. |

Si la revisión falla, publica además `tipo_falla` antes de terminar con error: `transitoria` cuando Ley Chile no respondió (timeout, conexión, HTTP 429 o 5xx) o respondió con un cuerpo vacío o una página HTML en vez de JSON, como durante sus mantenciones; `formato` cuando respondió con algo que la revisión no sabe leer (JSON ilegible, HTTP 4xx o metadatos sin título, tipo de versión, fecha de versión, vigencia actual o vigencias) y `error` ante cualquier otra falla. Los workflows usan esta señal para avisar por issue todo lo que no sea transitorio, y también una falla transitoria que se repite tres días seguidos. El mensaje del error indica la norma y qué respondió Ley Chile (código HTTP, tipo de contenido, largo y título de la página, o los campos que faltaron).

## Desarrollo

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Las decisiones y resguardos se documentan en [docs/decisiones-tecnicas.md](docs/decisiones-tecnicas.md). El [análisis de `ngosang/trackerslist`](docs/analisis-trackerslist.md) registra los patrones de automatización evaluados, las alternativas descartadas y la decisión de publicar artefactos verificables e inmutables. El [plan de migración a repositorio privado](docs/plan-privatizacion.md) describe la autenticación, los controles contra filtraciones y el despliegue coordinado previstos. El código publicado actualmente está bajo licencia MIT.
