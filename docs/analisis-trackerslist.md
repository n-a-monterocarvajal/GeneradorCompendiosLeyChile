# Análisis de `ngosang/trackerslist`

Fecha de observación: 5 de agosto de 2026.

## Propósito

Se revisó [`ngosang/trackerslist`](https://github.com/ngosang/trackerslist) como ejemplo de repositorio cuyos resultados se regeneran automáticamente. El objetivo fue identificar patrones de publicación y funciones de GitHub reutilizables para los compendios jurídicos, sin trasladar decisiones que reduzcan su trazabilidad o puedan distribuir documentos jurídicos obsoletos.

## Implementación observable

El repositorio contiene los resultados generados, una lista de exclusión, el README, la licencia, la configuración de GitHub Pages y `FUNDING.yml`. No contiene el código del bot ni un workflow propio que regenere las listas.

En la fecha de observación:

- el único workflow visible era el despliegue administrado de GitHub Pages;
- el bot realizaba un commit diario directamente sobre `master`;
- los commits automáticos figuraban con el propietario como autor y committer y no estaban firmados;
- no había releases, rulesets, Dependabot, Discussions ni plantillas de issues;
- Issues estaba habilitado y el README invitaba a reportar fuentes nuevas o defectuosas;
- el repositorio usaba topics para facilitar su descubrimiento;
- el README mostraba badges de fecha de actualización y cantidad de elementos mediante URLs estáticas de Shields.io reescritas por la automatización, además de un badge dinámico de estrellas;
- los resultados se publicaban en variantes filtradas por protocolo y también en variantes con direcciones IP;
- cada archivo tenía tres vías de acceso: `raw.githubusercontent.com`, GitHub Pages y jsDelivr.

Por lo visible públicamente, la regeneración se ejecuta fuera de este repositorio: podría ser un cron externo, una automatización alojada en otro repositorio o un servicio privado. No es posible auditar desde `trackerslist` cómo se obtienen, verifican o publican sus resultados.

## Patrones aprovechables

### Dirección pública estable

GitHub Pages permite ofrecer una portada estable y comprensible sin obligar a la persona usuaria a navegar por la interfaz del repositorio. Para los compendios podría mostrar la última revisión, la última actualización efectiva, las normas incluidas, los cambios de la última publicación y un botón hacia el asset canónico de GitHub Releases.

Pages no debe convertirse en el almacén canónico de los PDF. GitHub recomienda Releases para archivos descargables y Pages tiene límites de tamaño, compilación y ancho de banda. Una eventual página solo publicaría HTML y metadatos pequeños.

### Metadatos generados junto con el resultado

La fecha y la cantidad de elementos del README de `trackerslist` permiten comprobar rápidamente la frescura y dimensión del producto. El equivalente útil para los compendios es un manifiesto pequeño con:

- repositorio y commit de origen;
- workflow run que produjo el documento;
- tag, nombre y SHA-256 del PDF;
- fecha de revisión y fecha de generación;
- cantidad de normas incluidas;
- `idNorma` que motivaron la publicación.

No se necesita reescribir badges en cada ejecución. Los badges de release y workflows ya utilizados por los repositorios particulares son más fiables; el manifiesto puede alimentar en el futuro una página o consumidores automatizados.

### Canal de reportes

La invitación a abrir issues es útil, pero un formulario estructurado sería mejor que el enlace genérico de `trackerslist`. Un reporte de norma desactualizada debería solicitar como mínimo `idNorma`, URL oficial de Ley Chile, versión o fecha observada y descripción de la discrepancia.

### Descubrimiento

Los topics y el campo `homepage` son metadatos sencillos con valor real. Los repositorios particulares pueden usar topics como `chile`, `derecho`, `ley-chile`, `bcn`, `legislacion`, `compendio`, `pdf`, `derecho-penal` o `derecho-ambiental`.

## Patrones descartados

### Commits programados aunque no exista un cambio jurídico

Los compendios conservan el modelo dirigido por eventos: la revisión semanal solo publica cuando cambia alguna señal normativa normalizada. Esto reduce ejecuciones, tráfico hacia Ley Chile y ruido en el historial.

### Publicación directa de resultados en la rama principal

Los PDF no se incorporan a Git porque aumentarían permanentemente el tamaño del repositorio. GitHub Releases sigue siendo el canal canónico y versionado.

### Motor no auditable con identidad personal

La automatización debe quedar definida como código versionado, con permisos mínimos de `GITHUB_TOKEN`, dependencias fijadas y runs enlazados desde cada publicación. No se usarán credenciales personales para simular commits del propietario.

### Mirrors de terceros para el PDF vigente

Un CDN puede conservar contenido antiguo en caché. Para documentos jurídicos, varios mirrors sin una política explícita de frescura aumentan el riesgo de presentar una versión obsoleta como actual. La release de GitHub será la fuente canónica; cualquier futura réplica deberá ser versionada y verificable mediante SHA-256.

### Badges de vanidad

Estrellas, forks y conteos de descargas no acreditan actualidad ni integridad jurídica. Los badges se limitan a versión, estado de revisión, estado de generación y licencia.

## Comparación con los compendios

Los repositorios particulares ya superan el patrón observable de `trackerslist` en varios aspectos: tienen releases versionadas, notas que identifican los cambios normativos, generación disparada solo por cambios, rulesets, Dependabot, protección contra secretos y workflows públicos. La brecha principal estaba en la integridad posterior a la generación: un PDF publicado podía ser reemplazado y no incluía una verificación independiente de procedencia.

## Decisión adoptada: publicación verificable e inmutable

La publicación de cada repositorio particular sigue esta secuencia:

1. generar exactamente un PDF con nombre normalizado;
2. comprobar tamaño mínimo, cabecera, estructura y páginas mediante `pypdf` en modo estricto;
3. calcular SHA-256 y producir un archivo `.sha256`;
4. subir PDF y checksum como artefactos del run antes de publicar;
5. crear una attestation de procedencia de GitHub para el PDF;
6. crear la release como borrador con PDF, checksum, notas, digest y enlace al run;
7. publicar el borrador y marcarlo como `latest` únicamente si todas las barreras anteriores terminaron correctamente;
8. aplicar la inmutabilidad de GitHub a las releases futuras.

La inmutabilidad se habilita solo después de comprobar una ejecución completa del nuevo flujo. GitHub no la aplica retroactivamente. Una release publicada ya no podrá reemplazar ni eliminar sus assets o su tag; los borradores continúan siendo modificables, lo que permite preparar todos los assets antes de hacerlos públicos.

Las attestations requieren `id-token: write` y `attestations: write`, además del permiso de contenido necesario para crear la release. La verificación pública se realiza con:

```bash
gh attestation verify NOMBRE_DEL_PDF --repo PROPIETARIO/REPOSITORIO
```

## Mejoras posteriores, no incluidas en esta implementación

- una portada pequeña en GitHub Pages alimentada por un manifiesto de publicación;
- Issue Forms para reportar normas desactualizadas o proponer fuentes;
- topics y `homepage` para descubrimiento;
- un manifiesto JSON estable para consumidores automáticos.

## Referencias

- [`ngosang/trackerslist`](https://github.com/ngosang/trackerslist)
- [README de `trackerslist`](https://github.com/ngosang/trackerslist/blob/master/README.md)
- [Historial de commits de `trackerslist`](https://github.com/ngosang/trackerslist/commits/master/)
- [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages)
- [Límites de GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
- [Attestations de artefactos](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
- [Releases inmutables](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes)
- [Plantillas e Issue Forms](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates)
