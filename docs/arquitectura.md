# Arquitectura

```text
Repositorio particular
  ├─ app/fuentes.json
  ├─ app/requirements.txt (pin inmutable del motor)
  ├─ workflow revisar-versiones
  └─ workflow generar-compendio
             │
             ▼
GeneradorCompendiosLeyChile
  ├─ validador de configuración
  ├─ obtenedor BCN/Playwright
  ├─ ensamblador PDF
  └─ monitor de versiones JSON
             │
             ▼
Ley Chile / Biblioteca del Congreso Nacional
```

El estado de versiones pertenece al repositorio particular porque depende de su lista de `idNorma`. El código y las decisiones técnicas pertenecen al repositorio general.
