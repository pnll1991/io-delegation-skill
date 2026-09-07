# I/O Delegation

[English](README.md) | **Español**

### Menos contexto innecesario. Más atención a lo que importa

[![Pruebas offline](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)

[Skill](skills/io-delegation/SKILL.md) · [Instalación](docs/INSTALLATION.md) · [Benchmark](#benchmark) · [Adaptadores](skills/io-delegation/references/ADAPTERS.md)

Una skill portable para separar exploración, trabajo repetitivo y decisiones de ingeniería. Funciona como instrucciones para **Claude Code, Codex, Cursor y otros agentes que lean Markdown**. Incluye un ejecutor opcional para auxiliares locales o remotos aprobados.

El principal conserva arquitectura, depuración, decisiones sensibles, edición exacta y verificación. Una búsqueda o una lectura corta tienen prioridad sobre delegar por rutina.

```text
Localizar → clasificar → herramienta, lectura dirigida o auxiliar
          → verificar evidencia → razonar, editar e integrar
```

## Instalar

```bash
git clone https://github.com/pnll1991/io-delegation-skill.git
cd io-delegation-skill
```

Elegí el comando de tu agente y reemplazá la ruta por un proyecto existente:

```bash
python install.py --agent claude-code --project "RUTA_DEL_PROYECTO"
python install.py --agent codex --project "RUTA_DEL_PROYECTO"
python install.py --agent cursor --project "RUTA_DEL_PROYECTO"
```

No ejecutes los tres por rutina: Codex y Cursor comparten `.agents/skills/io-delegation/`. Claude Code usa `.claude/skills/io-delegation/`. El instalador requiere Python 3.10+, copia la carpeta completa y no sobrescribe instalaciones existentes. No modifica reglas, permisos, modelos ni configuraciones del agente.

Para instalar a nivel usuario, reemplazá `--project "..."` por `--global`. Para simular, añadí `--dry-run`. En Windows podés usar `py -3`; en otros entornos, `python3`.

Sin Python, copiá manualmente la carpeta completa `skills/io-delegation/` al destino correspondiente. Las instrucciones no necesitan Python ni un segundo modelo. Las instalaciones globales son locales a esa máquina; un agente remoto necesita su propia copia accesible.

## Usar

> Aplicá la skill io-delegation a esta tarea. Localizá primero los archivos relevantes. Priorizá herramientas deterministas y lecturas dirigidas. Delegá solo a un auxiliar disponible, aprobado y con contexto separado. Verificá evidencia antes de decidir. Sin auxiliar, continuá con lectura selectiva.

Para un agente sin descubrimiento de skills, indicá la ruta real a `SKILL.md` y pedile que lo lea. Consultá [activación y convivencia](docs/INSTALLATION.md).

## Probar sin configurar un modelo

Estos ejemplos funcionan con archivos del propio repositorio y no llaman a servicios:

```bash
python skills/io-delegation/scripts/io_delegate.py inspect --root . --paths install.py
python skills/io-delegation/scripts/io_delegate.py bulk-read --root . --paths install.py --question "¿En qué carpetas puede escribir el instalador?" --dry-run
python -m unittest discover -s tests -v
```

`bulk-read` devuelve hechos acotados con evidencia literal y cobertura declarada. `code-write` guarda un archivo nuevo en `.io-delegation/candidates/`; nunca lo aplica ni ejecuta automáticamente. El transporte real se configura siguiendo [ADAPTERS.md](skills/io-delegation/references/ADAPTERS.md). No se activa ningún proveedor por defecto.

## Benchmark

### Prueba real con un modelo local · 7 de septiembre de 2026

**Se midieron menos tokens en lecturas grandes, pero no una mejora de extremo a extremo que aprobara el criterio de calidad.** Ejecutamos `Qwen/Qwen2.5-Coder-1.5B-Instruct` en CPU con archivos reales del repo: lectura completa, lectura focalizada sin skill y lectura focalizada con **toda la skill cargada**. Es un ensayo controlado, no una sesión autónoma de Claude Code, Codex o Cursor.

Tokens observados de **entrada + salida**, incluidas las respuestas rechazadas por el control de calidad:

| Caso | Archivo completo | Focalizado, sin skill | Focalizado + skill completa | Reducción frente al archivo completo |
| --- | ---: | ---: | ---: | ---: |
| Constantes del ejecutor | 6.902 | 240 | 2.132 | 69,11% |
| Valores de configuración | 6.908 | 791 | 2.697 | 60,96% |
| Instalador pequeño — control negativo | 770 | 653 | 2.545 | -230,52% |

**Calidad:** el criterio definido antes de ejecutar exigía JSON sin envoltorios. Solo **1 de 9** respuestas principales pasó: la consulta de configuración focalizada sin skill. Ocho agregaron bloques Markdown, incluidas las tres respuestas con skill. Una revisión adicional **posterior al ensayo** encontró los valores correctos en las nueve al quitar esos bloques; eso **no convierte los fallos originales en aprobados**. No se midieron llamadas de corrección ni su costo.

**Control de delegación:** el ejecutor real `bulk-read` consultó al mismo modelo, que devolvió `insufficient_context` sin hallazgos. El ensayo volvió a la lectura dirigida. Auxiliar + respuesta alternativa consumieron **9.356 tokens**, frente a **6.902** de la lectura completa: **35,55% más**. El contexto del principal bajó, pero el consumo total no. La respuesta alternativa también falló el formato estricto.

**Conclusión:** leer solo lo necesario reduce mucho el contexto; cargar la skill para una tarea pequeña puede empeorar el consumo. Una lectura ya focalizada sin skill sigue siendo más económica. El ensayo no demuestra selección automática, cambios de código correctos, ahorro de facturación ni un porcentaje fijo para los agentes compatibles.

[Metodología, fallos y reproducción](benchmarks/README.md) · [Resultados del modelo](benchmarks/results/2026-09-07-live.json) · [Ejecución terminada](https://github.com/pnll1991/io-delegation-skill/actions/runs/34154914478)

### Conteo independiente de tokens

Otro ensayo de cinco casos contó los mensajes serializados con `tiktoken` 0.11.0. Los tres casos grandes redujeron **64,03–72,00%** los tokens `o200k_base`, incluyendo la skill completa. Los controles pequeños y ya focalizados aumentaron el consumo. Son conteos BPE de esa serialización, **no** los tokens de inferencia Qwen de la tabla anterior ni una factura o el tokenizador de Claude.

[Datos de ambos tokenizadores y controles negativos](benchmarks/results/2026-09-07-census.json) · [Ejecución del conteo](https://github.com/pnll1991/io-delegation-skill/actions/runs/34155367336)

```bash
# Solo conteo: sin descargar un modelo ni ejecutar inferencia
python -m pip install tiktoken==0.11.0
python benchmarks/run.py --output benchmark-output
```

El piloto de modelos se ejecuta manualmente; el conteo liviano corre ante cambios relevantes. Sus dependencias no son necesarias para instalar ni usar la skill.

## Qué incluye

La carpeta instalable contiene la skill, el script, ejemplos y referencias de flujo, adaptadores, validación y fuentes. El repositorio agrega instalador, 84 pruebas offline (75 originales y 9 del benchmark), CI, documentación, licencia MIT y guías de contribución.

La ejecución Linux previa a publicar aprobó 75 pruebas. El estado real de la matriz de GitHub está en el badge; los tests de infraestructura no demuestran selección automática en agentes ni ahorro con modelos. Ver [alcance de pruebas](docs/TESTING.md).

Inspirada en Spotify Engineering y `shunt`, con contratos y controles propios. [Procedencia y diferencias](skills/io-delegation/references/SOURCES.md). No hay afiliación con Spotify ni se redistribuye su artículo.

No promete un porcentaje fijo de ahorro: hay que medir principal, auxiliar, latencia y retrabajo. Leer [VALIDATION.md](skills/io-delegation/references/VALIDATION.md) y [SECURITY.md](SECURITY.md).

[MIT](LICENSE) · Versión 0.1.0 · [Contribuir](CONTRIBUTING.md)
