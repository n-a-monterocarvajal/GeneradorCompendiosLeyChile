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

### 5.1 Reclasificaciones de vigencias históricas

Ley Chile corrige de tanto en tanto los límites de fecha entre versiones ya superadas. El 27 de agosto de 2026 movió el corte entre dos vigencias de 1991 del Decreto N.º 55, de 1977 (`idNorma=8355`); no hubo decreto modificatorio y el texto descargado resultó idéntico salvo el sello de exportación que BCN estampa en cada página. Regenerar el compendio por ese ajuste sólo agrega ruido al historial de releases.

Cada cambio se clasifica entonces como significativo o informativo. Es significativo cuando afecta lo que se descarga hoy: cambia `vigencia_actual`, aparece o desaparece una vigencia, cambian los eventos pendientes o la alerta de texto diferido, cambian `fecha_version` o `fecha_actualizacion_texto`, o el cambio de huella no puede explicarse con las señales anteriores. Es informativo cuando sólo se corrieron fechas de vigencias cuyo término ya pasó y que no comparten inicio con la vigencia actual.

Un cambio informativo se guarda en `estado-versiones.json` —para conservar la trazabilidad y no volver a reportarlo— y se describe en el resumen bajo un título propio, pero deja `changes_detected=false` y no despacha `generar-compendio.yml`. Ante cualquier duda se responde «significativo»: una vigencia sin término, con fecha ilegible, futura o que comparte inicio con la vigencia actual se trata como vigente.

Distinguir un corrimiento de límite de una vigencia realmente nueva exige emparejar bien los registros. Cuando BCN mueve el corte entre dos tramos contiguos, el primero conserva su inicio y el segundo su término, de modo que las vigencias pendientes se emparejan primero por `desde` y después por `hasta`. Sin la segunda pasada, el corrimiento aparecía como la desaparición de un tramo y la aparición de otro, es decir, como una condición que sí publica.

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
