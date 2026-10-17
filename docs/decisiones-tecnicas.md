# Decisiones técnicas

## 1. El obtenedor es un componente común

Los compendios temáticos no deben mantener copias divergentes del descargador. Este repositorio publica un paquete Python versionado; cada repositorio particular fija una revisión inmutable y conserva solo la configuración de fuentes y la automatización de publicación.

## 2. Resguardos ante fallos transitorios de Ley Chile

Una descarga puede fallar de forma no determinista por variaciones del sitio, la sesión o la respuesta. Se implementan conjuntamente:

- hasta tres intentos configurables por norma;
- espera exponencial breve con variación aleatoria;
- contexto Chromium nuevo después de cada fallo;
- reinicio preventivo del contexto cada seis normas por defecto;
- conservación de diagnósticos HTML y PNG por intento;
- validación de tamaño, cabecera, estructura PDF y respuesta HTTP;
- cierre garantizado de páginas, sesiones HTTP, listeners, contextos y navegador;
- fallo definitivo visible después de agotar los intentos, sin producir un PDF parcial.

El reintento completo de GitHub Actions se conserva como segunda barrera operativa. No sustituye los reintentos acotados por norma.

## 3. Marcadores PDF degradables

Los marcadores internos de un PDF de origen pueden apuntar a una página inexistente o ser incompatibles con `pypdf`. El marcador principal de cada norma es obligatorio; un marcador interno inválido se omite sin abortar todo el compendio.

## 4. Validación temprana de configuración

Antes de abrir Chromium se comprueban tipos de fuente, unicidad de `id_norma` y nombres de archivo, dominios permitidos, concordancia entre URL e identificador y rangos de configuración. Un error estructural no se reintenta como si fuera una falla transitoria.

## 5. Detección de nuevas versiones

La revisión usa `get_norma_json` porque expone el inventario `metadatos.vigencias`, la vigencia actual, eventos pendientes y alertas de texto diferido. La huella se calcula sobre una representación normalizada y ordenada de esas señales.

No se compara HTML crudo: `ConsultaNormasBCN` demostró que contiene notas editoriales, enlaces específicos de versión y otras diferencias ruidosas. La primera revisión crea una línea base sin generar un compendio. Una revisión posterior dispara una publicación cuando:

- aparece o desaparece una vigencia;
- cambia la versión actualmente vigente;
- cambian los eventos pendientes;
- aparece, cambia o desaparece una alerta de texto diferido;
- cambian los metadatos estables de versión observados.

Si una fuente no puede revisarse después de los reintentos, la ejecución falla y no reemplaza el estado anterior. La ausencia temporal de datos nunca se interpreta como una actualización.

La falla distingue su causa. Un timeout, un error de conexión o una respuesta HTTP 429 o 5xx es transitorio: el sitio no respondió y la revisión del día siguiente lo vuelve a intentar sin que nadie intervenga. Cualquier otra respuesta que no se pueda leer indica que Ley Chile cambió el servicio. Para no confundir un cambio de forma con una actualización normativa, la respuesta debe traer el título, el tipo y la fecha de versión, la vigencia actual con su inicio y al menos una vigencia; sin esas señales la huella dejaría de ser comparable y se publicaría un compendio sin cambios reales. Todas las normas de los compendios las informaban al 11 de octubre de 2026.

### 5.1 Reclasificaciones de vigencias históricas

Ley Chile corrige de tanto en tanto los límites de fecha entre versiones ya superadas. El 27 de agosto de 2026 movió el corte entre dos vigencias de 1991 del Decreto N.º 55, de 1977 (`idNorma=8355`); no hubo decreto modificatorio y el texto descargado resultó idéntico salvo el sello de exportación que BCN estampa en cada página. Regenerar el compendio por ese ajuste sólo agrega ruido al historial de releases.

Cada cambio se clasifica entonces como significativo o informativo. Es significativo cuando afecta lo que se descarga hoy: cambia `vigencia_actual`, aparece o desaparece una vigencia, cambian los eventos pendientes o la alerta de texto diferido, cambian `fecha_version` o `fecha_actualizacion_texto`, o el cambio de huella no puede explicarse con las señales anteriores. Es informativo cuando sólo se corrieron fechas de vigencias cuyo término ya pasó y que no comparten inicio con la vigencia actual.

Un cambio informativo se guarda en `estado-versiones.json` —para conservar la trazabilidad y no volver a reportarlo— y se describe en el resumen bajo un título propio, pero deja `changes_detected=false` y no despacha `generar-compendio.yml`. Ante cualquier duda se responde «significativo»: una vigencia sin término, con fecha ilegible, futura o que comparte inicio con la vigencia actual se trata como vigente.

Distinguir un corrimiento de límite de una vigencia realmente nueva exige emparejar bien los registros. Cuando BCN mueve el corte entre dos tramos contiguos, el primero conserva su inicio y el segundo su término, de modo que las vigencias pendientes se emparejan primero por `desde` y después por `hasta`. Sin la segunda pasada, el corrimiento aparecía como la desaparición de un tramo y la aparición de otro, es decir, como una condición que sí publica.

### 5.2 Norma modificatoria

`get_norma_json` no dice qué norma originó cada vigencia. El servicio `Consulta/get_versiones`, que usa la página de versiones de Ley Chile, sí lo informa: cada versión trae sus modificatorias con número, título, fecha de publicación e inicio de vigencia. Cuelga cada modificatoria de la versión que termina y no de la que empieza, de modo que se emparejan por `@inicioVigencia` con el inicio de la versión nueva, nunca por la posición en la lista.

Ese servicio asocia una ley con vigencias escalonadas a una sola fecha. La Ley 21.713 generó versiones del Código Tributario el 1 de noviembre de 2024, el 1 de enero, el 24 de enero, el 25 de abril, el 1 de mayo y el 1 de julio de 2025, y `get_versiones` la nombra una sola vez. Para las demás fechas se consulta `Navegar/get_ultima_modificatoria`, que solo se acepta si declara el mismo inicio de vigencia. Algunas versiones no tienen modificatoria en ninguno de los dos, como la del 28 de noviembre de 2025 en los códigos Penal y Procesal Penal; el resumen dice entonces que Ley Chile no la informa.

La consulta se hace solo para las normas con cambios significativos y nunca hace fallar la revisión: si Ley Chile no responde, el resumen lo indica y el resto sigue igual. El dato no entra en la huella ni en el estado, de modo que una corrección posterior de Ley Chile no publica un compendio.

### 5.3 Derogación y refundición

`get_norma_json` informa el estado de la norma como fuente: `metadatos.derogado`, `metadatos.derogacion_tacita` y alertas de clase `derogado` («Esta norma ha sido derogada el 12-FEB-2025», en la Ley 18.314) o `refundido` («Esta norma ha sido refundida por DFL-1 29-OCT-2009», en la Ley 18.290). La norma derogatoria, en cambio, no trae ningún campo que diga qué derogó, así que se vigila la norma que está en el compendio.

Una norma derogada conserva su texto completo en la última versión: un compendio regenerado la incluiría como si siguiera vigente. Por eso el cambio de estado se destaca en el resumen y se publica `avisos_norma` para que el workflow abra un issue. Quitarla o reemplazarla en `fuentes.json` queda como decisión humana, porque a veces conviene conservarla durante una transición.

El estado se guarda fuera de la huella. Así, la versión del motor que lo incorpora no hace parecer cambiadas todas las normas de un estado anterior: la primera revisión lo completa y desde la siguiente se compara.

### 5.4 Versión vigente en la descarga

El 16 de octubre de 2026 la página de la Ley de Tránsito siguió exportando la versión intermedia del 27 de agosto después de que entrara en vigor la del 16 de octubre. Durante unas horas el JSON la marcó con `alerta_global` («El texto de esta versión no se encuentra vigente», con la fecha de la vigente en `e.f`), y después informó la vigente en `metadatos.vigencia` mientras la página seguía ofreciendo la anterior. El generador ahora consulta el JSON antes de descargar cada norma, toma la fecha de la alerta o, si no hay, el inicio de la vigencia actual, y la fija en el enlace de exportación (`hddResultadoExportar=<idNorma>.<fecha>.0.0`).

### 5.5 Fechas centinela de 2222

Ley Chile usa el 2 de febrero de 2222 como inicio de las vigencias que dependen de un evento sin fecha conocida, y el 1 de febrero de 2222 como término de la vigencia anterior. El estado conserva esas fechas tal como llegan, porque forman parte del inventario que se compara; solo el resumen de la revisión las traduce a «sin fecha de término definida» y «desde una fecha aún no determinada».

Cuando la versión actual de una norma empieza en esa fecha, como la Ley N.º 20.084 desde octubre de 2026, el compendio publica igualmente la versión que muestra la web de Ley Chile: prima lo que Ley Chile ofrece como texto de la norma. Las notas del release lo advierten en «Notas sobre versiones» e indican la última versión con fecha de inicio.

### 5.6 Campos que no se usan

Del resto de `get_norma_json` no se usan los datos de identificación (`tipos_numeros`, `organismos`, fechas de publicación y promulgación, `materias`), las alertas de doble articulado y de texto que refunde a otro, `vinculaciones`, `resumenes`, `categorias_norma`, `jurisprudencia` ni `doctrina`, porque no cambian el texto que se descarga. Tampoco `proyectos`: lista todos los proyectos de modificación asociados a la norma (320 en el Código Procesal Penal), sin indicar cuáles siguen en trámite, así que un aviso basado en él no sería confiable.

## 6. Diferidos

Para decidir si existe texto diferido se usa la alerta estructurada `clase=diferido`. La versión especial `idVersion=Diferido` es útil para localizar partes modificadas mediante texto previamente saneado, como documenta e implementa `ConsultaNormasBCN`, pero no es necesaria para decidir si debe regenerarse el compendio: los cambios del inventario de vigencias o de la alerta ya activan la revisión.

Se evita parsear el mensaje humano de la alerta como fuente de verdad. Su texto se conserva únicamente para diagnóstico y notas de publicación.

## 7. Publicaciones motivadas por cambios

Los repositorios particulares dejan de generar mensualmente. Un workflow semanal revisa versiones y, solo ante cambios significativos, despacha una ejecución separada del generador. El estado persistente se actualiza también cuando el único cambio es informativo, de modo que la revisión siguiente no vuelva a reportarlo: el workflow decide con `state_updated` si debe versionar el estado y con `changes_detected` si debe publicar. Las notas del release enumeran las normas y señales modificadas. La generación manual continúa disponible.

## 8. Trazabilidad con ConsultaNormasBCN

Se reutilizaron, en modo de solo lectura, sus hallazgos sobre `get_norma_json`, `metadatos.vigencias`, alertas diferidas y la necesidad de sanear antes de comparar contenido. No se copiaron componentes WinUI ni se modificó aquel repositorio.

## 9. Publicaciones verificables e inmutables

Antes de publicar, cada repositorio particular valida el PDF completo, calcula su SHA-256, conserva el checksum como asset y solicita a GitHub una attestation vinculada al commit y al workflow run. La release se prepara como borrador y solo se publica después de superar todas esas barreras.

Las releases futuras se configuran como inmutables. El tag y los assets publicados no se reemplazan ni eliminan; cualquier corrección produce una nueva release. Los borradores siguen siendo el área de preparación recuperable antes de la publicación.

Esta decisión y las alternativas evaluadas se detallan en [analisis-trackerslist.md](analisis-trackerslist.md).
