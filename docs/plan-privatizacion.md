# Plan de migración del motor a repositorio privado

## Estado y objetivo

Estado: propuesto; no ejecutar el cambio de visibilidad hasta completar los preparativos y las pruebas descritas aquí.

El objetivo es mantener públicos los compendios particulares y sus PDF, pero restringir el acceso al código fuente y al historial futuro de `GeneradorCompendiosLeyChile`. La privatización busca elevar la barrera de reutilización abusiva del obtenedor, sin interrumpir la revisión semanal, la generación motivada por cambios ni Dependabot.

La visibilidad privada es una medida de reducción de exposición, no un control suficiente contra el abuso de Ley Chile. Los límites de concurrencia, reintentos acotados, espera exponencial y descargas secuenciales continúan siendo obligatorios.

## Limitaciones conocidas

- El código ya publicado bajo licencia MIT pudo ser clonado o archivado. Cambiar la visibilidad no retira copias existentes ni revoca retroactivamente los permisos ya concedidos. Cualquier cambio de licencia para versiones futuras requiere una decisión separada y, si es relevante jurídicamente, asesoría especializada.
- Los repositorios particulares seguirán revelando el nombre del paquete, la revisión fijada, las interfaces de línea de comandos, la lista de fuentes y el comportamiento observable del sistema.
- El código privado debe descargarse y ejecutarse en un runner temporal. Un workflow autorizado o una dependencia comprometida podría intentar extraerlo; por eso se deben proteger los workflows y reducir sus permisos.
- No se puede usar `GITHUB_TOKEN` para leer el motor: ese token está limitado al repositorio particular que ejecuta el workflow.

## Arquitectura de acceso propuesta

1. Mantener `Compendio-Derecho-Penal-Chile` y `Compendio-Derecho-Ambiental-Chile` públicos.
2. Convertir `GeneradorCompendiosLeyChile` en privado solamente al final de la migración.
3. Crear una GitHub App dedicada e instalarla únicamente sobre el repositorio del motor.
4. Conceder a la App solo permiso de repositorio `Contents: read`; no conceder administración, workflows ni escritura.
5. Guardar el identificador de la App como variable y su clave privada como secreto de Actions en cada repositorio particular.
6. Generar en cada ejecución un token de instalación temporal y usarlo solo durante `pip install` para resolver la URL Git ya fijada a un SHA inmutable.
7. No insertar credenciales en `requirements.txt`, commits, argumentos visibles, artefactos ni logs. La configuración Git temporal debe apuntar exclusivamente a la URL exacta del motor y eliminarse inmediatamente después de instalarlo.

No se recomienda compartir el motor como una Action privada ni conceder acceso amplio a GitHub Packages desde repositorios públicos. El consumo seguirá siendo una dependencia Git autenticada y fijada por SHA.

## Dependabot

Los secretos normales de Actions no están disponibles para Dependabot. Para conservar las revisiones `pip` en los repositorios particulares se requiere una credencial separada:

- crear un token de acceso detallado dedicado, con expiración, limitado al repositorio del motor y con `Contents: read`;
- almacenarlo como secreto de Dependabot, nunca como texto en `dependabot.yml`;
- declarar el repositorio Git privado en `dependabot.yml` y verificar primero el acceso en un solo repositorio particular;
- rotar el token según su expiración y documentar responsable y fecha de renovación.

Dependabot para GitHub Actions no depende del motor privado y debe continuar habilitado. El propio motor conserva su Dependabot para actualizar sus dependencias internas.

## Controles contra filtraciones

Antes de privatizar se debe comprobar que:

- los workflows no usan `pull_request_target` ni entregan secretos a código procedente de forks;
- las modificaciones de `.github/workflows`, `app/requirements.txt` y la configuración de Dependabot están protegidas mediante rulesets y revisión explícita;
- el token temporal se enmascara y nunca se imprime; no se habilita trazado de shell durante la autenticación;
- los errores públicos muestran contexto operativo, pero no líneas de código, variables de entorno ni contenido de `site-packages`;
- los artefactos de diagnóstico usan listas permitidas de archivos y nunca incluyen el entorno Python, cachés de `pip`, configuración Git ni credenciales;
- los jobs conservan permisos mínimos y concurrencia única;
- las revisiones del motor se fijan mediante SHA completo y se actualizan por PR auditable;
- la clave de la GitHub App y el token de Dependabot tienen un procedimiento de revocación y rotación probado.

## Secuencia de migración

1. Auditar logs, trazas, artefactos, permisos y disparadores de los workflows particulares.
2. Aplicar los resguardos de logs y artefactos antes de introducir secretos.
3. Crear e instalar la GitHub App de solo lectura.
4. Configurar variables y secretos de Actions en ambos repositorios particulares.
5. Configurar la credencial y el acceso privado de Dependabot.
6. Actualizar los workflows para autenticar exclusivamente la instalación del motor y borrar la configuración temporal tras `pip install`.
7. Probar la instalación autenticada mientras el motor todavía es público, comprobando que ningún secreto aparezca en logs.
8. Cambiar `GeneradorCompendiosLeyChile` a privado.
9. Ejecutar manualmente en cada repositorio particular la revisión de versiones y una generación completa.
10. Verificar release, PDF, notas, artefactos, reintento y ausencia de filtraciones.
11. Ejecutar o esperar una revisión de Dependabot y confirmar que resuelve la dependencia privada.
12. Actualizar los README particulares para describir el motor privado sin ofrecer un enlace inaccesible como documentación pública.

## Criterios de aceptación

- Ambos particulares instalan exactamente el SHA autorizado del motor privado.
- Una persona sin acceso al motor recibe una respuesta de repositorio inexistente y no puede descargar código ni archivos fuente desde los particulares.
- Los runs semanales y manuales terminan correctamente sin credenciales visibles.
- Dependabot continúa operativo para `pip` y GitHub Actions.
- Los artefactos contienen únicamente PDF, resúmenes y diagnósticos expresamente permitidos.
- Revocar la instalación de la App impide de inmediato una nueva instalación del motor.
- Los resguardos de ritmo y concurrencia permanecen activos.

## Reversión

Si falla la instalación después del cambio de visibilidad, se debe detener la generación, recopilar únicamente diagnósticos saneados y elegir una de estas reversiones:

1. restaurar temporalmente la visibilidad pública del motor mientras se corrige la autenticación; o
2. revertir los workflows a la última revisión funcional y mantener suspendidos los runs automáticos.

La reversión no debe sustituir el SHA fijado por una rama móvil ni ampliar los permisos de la App o del token para resolver rápidamente un fallo.
