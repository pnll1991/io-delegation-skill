# I/O Delegation

[English](README.md) | **Español**

### Menos contexto innecesario. Más atención a lo que importa

[![Pruebas offline](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)

[Skill](skills/io-delegation/SKILL.md) · [Instalación](docs/INSTALLATION.md) · [Control de lecturas](#control-opcional-de-lecturas) · [Benchmark](#benchmark) · [Adaptadores](skills/io-delegation/references/ADAPTERS.md)

Una skill portable para separar exploración, trabajo repetitivo y decisiones de ingeniería. Funciona como instrucciones para **Claude Code, Codex, Cursor y otros agentes que lean Markdown**. Incluye un ejecutor opcional para auxiliares locales o remotos aprobados.

El principal conserva arquitectura, depuración, decisiones sensibles, edición exacta y verificación. Una búsqueda o una lectura corta tienen prioridad sobre delegar por rutina. La versión 0.2 agrega hooks opcionales para controlar lecturas sin convertirlos en un requisito del núcleo.

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

No ejecutes los tres por rutina: Codex y Cursor comparten `.agents/skills/io-delegation/`. Claude Code usa `.claude/skills/io-delegation/`. El instalador requiere Python 3.10+, copia la carpeta completa y no sobrescribe instalaciones existentes. No modifica reglas, permisos, modelos ni configuraciones del agente. Una copia instalada de 0.1 requiere una actualización manual revisada, conservando tus personalizaciones.

Para instalar a nivel usuario, reemplazá `--project "..."` por `--global`. Para simular, añadí `--dry-run`. En Windows podés usar `py -3`; en otros entornos, `python3`.

Sin Python, copiá manualmente la carpeta completa `skills/io-delegation/` al destino correspondiente. Las instrucciones no necesitan Python ni un segundo modelo; los instaladores, el ejecutor y los hooks opcionales sí requieren Python. Las instalaciones globales son locales a esa máquina; un agente remoto necesita su propia copia accesible.

## Usar

> Aplicá la skill io-delegation a esta tarea. Localizá primero los archivos relevantes. Priorizá herramientas deterministas y lecturas dirigidas. Delegá solo a un auxiliar disponible, aprobado y con contexto separado. Verificá evidencia antes de decidir. Sin auxiliar, continuá con lectura selectiva.

Para un agente sin descubrimiento de skills, indicá la ruta real a `SKILL.md` y pedile que lo lea. Consultá [activación y convivencia](docs/INSTALLATION.md).

## Control opcional de lecturas

La versión **0.2.0** incorpora la capa de control anterior a la herramienta. Un motor Python común revisa presupuestos de texto localmente; los adaptadores traducen su decisión al formato de hooks de cada agente.

| Modo | Comportamiento |
| --- | --- |
| Solo instrucciones | Orientación portable, sin interceptar herramientas. |
| Observación | Evalúa las lecturas y emite metadatos, pero no bloquea excesos de presupuesto. |
| Bloqueo | Rechaza lecturas cubiertas que exceden el presupuesto y propone alternativas. |

Después de instalar la skill, activá explícitamente la integración para el proyecto:

```bash
# Elegí claude-code, codex o cursor
python install_hooks.py --agent claude-code --project "RUTA_DEL_PROYECTO" --dry-run
python install_hooks.py --agent claude-code --project "RUTA_DEL_PROYECTO"
```

Agregá `--python python3` si ese es el ejecutable disponible para tu agente. **Cada agente necesita su propio registro de hooks**, aunque Codex y Cursor compartan la carpeta de la skill. El instalador normal no activa hooks.

| Agente | Configuración | Eventos y herramientas registrados |
| --- | --- | --- |
| Claude Code | `.claude/settings.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Codex | `.codex/hooks.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Cursor | `.cursor/hooks.json` | `preToolUse`: `Read`, `Shell` |

El presupuesto inicial permite hasta **350 líneas de origen y 64.000 bytes** por lectura inspeccionada o lote de archivos reconocido en un comando. También detecta archivos minificados por tamaño, comprueba el rango solicitado y no considera suficiente agregar un offset sin límite. Reconoce comandos literales como `cat`, `head`, `tail`, `Get-Content` y rangos acotados de `sed`.

Ante un bloqueo, propone buscar símbolos, leer un rango o usar un auxiliar ya aprobado cuando convenga. **No inicia modelos ni cambia proveedores por su cuenta.** Sin auxiliar, sigue disponible la lectura dirigida.

El instalador conserva los hooks y permisos existentes, respalda la configuración que modifica, comprueba el ejecutor local y permite `--dry-run` y `--remove`. No reemplaza silenciosamente un ejecutor modificado. La política está en `.io-delegation-hooks/policy.json`; `mode: "observe"` registra decisiones sin bloquear excesos. Los comandos usan rutas locales: hay que reinstalar al mover el proyecto o cambiar de máquina. Siguen siendo necesarias la confianza y aprobación normales del agente.

Para quitar solo el registro de esta integración, conservando los demás:

```bash
python install_hooks.py --agent claude-code --project "RUTA_DEL_PROYECTO" --remove
```

**La cobertura tiene límites.** No controla programas arbitrarios, herramientas no reconocidas o MCP, resultados de búsqueda, adjuntos del editor ni rutas que no disparan el hook registrado. El análisis de pipelines es conservador. No es un intérprete de shell completo, una sandbox ni un límite de tokens de toda la sesión; tampoco fuerza la generación de código. Instalar un hook no demuestra que el cliente lo haya ejecutado: el instalador informa `host_verified: false` hasta hacer una prueba real en ese agente.

[Política, alcance y verificación en el agente](skills/io-delegation/references/ENFORCEMENT.md) · [Pruebas de hooks](tests/test_read_guard.py)

## Probar sin configurar un modelo

Estos ejemplos funcionan con archivos del propio repositorio y no llaman a servicios:

```bash
python skills/io-delegation/scripts/io_delegate.py inspect --root . --paths install.py
python skills/io-delegation/scripts/io_delegate.py bulk-read --root . --paths install.py --question "¿En qué carpetas puede escribir el instalador?" --dry-run
python -m unittest discover -s tests -v
```

`bulk-read` devuelve hechos acotados con evidencia literal y cobertura declarada. `code-write` guarda un archivo nuevo en `.io-delegation/candidates/`; nunca lo aplica ni ejecuta automáticamente. El transporte real se configura siguiendo [ADAPTERS.md](skills/io-delegation/references/ADAPTERS.md). No se activa ningún proveedor por defecto.

La versión 0.2 agrega **51 pruebas** de presupuestos, rangos, comandos, controles negativos, entradas inválidas, protocolos en subprocesos, instalación, respaldo y eliminación del registro. Se conservan las pruebas anteriores del núcleo y del benchmark. GitHub Actions ejecuta la suite en Linux, Windows y macOS con Python 3.10 y 3.13; el badge muestra el estado real.

Estas pruebas ejecutan nuestros scripts con eventos de anfitrión sintéticos. **No abren sesiones autenticadas de Claude Code, Codex o Cursor ni demuestran ahorro de tokens.** La [guía de prueba real](skills/io-delegation/references/ENFORCEMENT.md#test-a-real-host-before-claiming-enforcement) separa esa validación de la comprobación de protocolos.

## Benchmark

### Piloto histórico de la versión 0.1 · 7 de septiembre de 2026

**Estos resultados son anteriores a los hooks de 0.2. No miden su eficacia ni sesiones autónomas.** No se atribuye un ahorro nuevo de extremo a extremo a la versión 0.2.

**Se midieron menos tokens en lecturas grandes, pero no una mejora de extremo a extremo que aprobara el criterio de calidad.** Ejecutamos `Qwen/Qwen2.5-Coder-1.5B-Instruct` en CPU con archivos reales del repo: lectura completa, lectura focalizada sin skill y lectura focalizada con **toda la skill 0.1 cargada**. Es un ensayo controlado, no una sesión autónoma de Claude Code, Codex o Cursor.

Tokens observados de **entrada + salida**, incluidas las respuestas rechazadas por el control de calidad:

| Caso | Archivo completo | Focalizado, sin skill | Focalizado + skill 0.1 completa | Reducción frente al archivo completo |
| --- | ---: | ---: | ---: | ---: |
| Constantes del ejecutor | 6.902 | 240 | 2.132 | 69,11% |
| Valores de configuración | 6.908 | 791 | 2.697 | 60,96% |
| Instalador pequeño — control negativo | 770 | 653 | 2.545 | -230,52% |

**Calidad:** el criterio definido antes de ejecutar exigía JSON sin envoltorios. Solo **1 de 9** respuestas principales pasó: la consulta de configuración focalizada sin skill. Ocho agregaron bloques Markdown, incluidas las tres respuestas con skill. Una revisión adicional **posterior al ensayo** encontró los valores correctos en las nueve al quitar esos bloques; eso **no convierte los fallos originales en aprobados**. No se midieron llamadas de corrección ni su costo.

**Control de delegación:** el ejecutor real `bulk-read` consultó al mismo modelo, que devolvió `insufficient_context` sin hallazgos. El ensayo volvió a la lectura dirigida. Auxiliar + respuesta alternativa consumieron **9.356 tokens**, frente a **6.902** de la lectura completa: **35,55% más**. El contexto del principal bajó, pero el consumo total no. La respuesta alternativa también falló el formato estricto.

**Conclusión:** leer solo lo necesario reduce mucho el contexto; cargar la skill para una tarea pequeña puede empeorar el consumo. Una lectura ya focalizada sin skill sigue siendo más económica. El ensayo no demuestra selección automática, cambios de código correctos, ahorro de facturación ni un porcentaje fijo para los agentes compatibles.

[Metodología, fallos y reproducción](benchmarks/README.md) · [Resultados del modelo](benchmarks/results/2026-09-07-live.json) · [Ejecución terminada](https://github.com/pnll1991/io-delegation-skill/actions/runs/34154914478)

### Conteo independiente de tokens

Otro ensayo de cinco casos contó los mensajes serializados con `tiktoken` 0.11.0. Los tres casos grandes redujeron **64,03–72,00%** los tokens `o200k_base`, incluyendo la skill 0.1 completa. Los controles pequeños y ya focalizados aumentaron el consumo. Son conteos BPE de esa serialización, **no** los tokens de inferencia Qwen de la tabla anterior ni una factura o el tokenizador de Claude.

[Datos de ambos tokenizadores y controles negativos](benchmarks/results/2026-09-07-census.json) · [Ejecución del conteo](https://github.com/pnll1991/io-delegation-skill/actions/runs/34155367336)

```bash
# Solo conteo: sin descargar un modelo ni ejecutar inferencia
python -m pip install tiktoken==0.11.0
python benchmarks/run.py --output benchmark-output
```

Ejecutar en main actual cuenta la skill actual. Para reproducir las cifras históricas, usá el `executed_commit` del informe, como explica la guía. El piloto de modelos se ejecuta manualmente; el conteo liviano corre ante cambios relevantes. Sus dependencias no son necesarias para instalar ni usar la skill.

## Qué incluye

La carpeta instalable contiene la skill, el ejecutor de auxiliares, el motor de control, ejemplos y referencias de flujo, adaptadores, validación y fuentes. El repositorio agrega instaladores separados para skill y hooks, pruebas, CI, documentación, licencia MIT y guías de contribución.

Inspirada en Spotify Engineering y `shunt`, con contratos y controles propios. [Procedencia y diferencias](skills/io-delegation/references/SOURCES.md). No hay afiliación con Spotify ni se redistribuye su artículo.

No promete un porcentaje fijo de ahorro: hay que medir principal, auxiliar, latencia y retrabajo. Leer [VALIDATION.md](skills/io-delegation/references/VALIDATION.md) y [SECURITY.md](SECURITY.md). El ejecutor y el control de lecturas no son sandboxes.

[MIT](LICENSE) · Versión 0.2.0 · [Contribuir](CONTRIBUTING.md)
