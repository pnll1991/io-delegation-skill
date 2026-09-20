# I/O Delegation

[English](README.md) | **Español**

### Dale a los agentes de código el contexto correcto, no todo el repositorio.

[![Offline tests](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](docs/INSTALLATION_V1.md)

**Claude Code · Codex · Cursor**

I/O Delegation es un context gateway local-first para agentes de código. Reduce la exposición innecesaria del repositorio buscando, extrayendo y enrutando evidencia acotada antes de que el modelo principal razone sobre ella.

El producto está diseñado alrededor del control del usuario:

- búsqueda y extracción determinística antes de llamar modelos;
- debugging, seguridad, arquitectura, edición y generación quedan en el agente principal;
- el routing con Jev es opcional;
- cambiar de modelo automáticamente no es el default;
- suggest / balanced es el modo de control por defecto;
- manual conserva el modelo elegido explícitamente por el usuario;
- auto es opt-in y es el único modo que puede cambiar perfiles de worker aprobados;
- la compactación de contexto es automática por defecto, con fallback nativo del host;
- el read guard es opcional y está apagado por defecto.

El gateway funciona sin TypeSafe, sin worker y sin cambiar el modelo que ya elegiste.

## Estado actual

| Área | Estado |
| --- | --- |
| Context gateway MCP | Implementado para Claude Code, Codex y Cursor |
| Superficie pública MCP | search, extract, query |
| Routing determinístico local | Default |
| Selector de ruta TypeSafe Jev | Opcional, opt-in explícito |
| Política host-aware de modelos | Codex + Cursor |
| Control de modelo default | suggest / balanced |
| Cambio dinámico de perfil | Solo auto |
| Compactación automática | Activada por defecto |
| Read guard | Opcional, apagado por defecto |
| Validación live de Codex | Completada con Codex real autenticado |
| Política Cursor | Implementada/testeada; todavía no calibrada live en una sesión Cursor real del usuario |
| Claim de ahorro en producción | Ninguno: la evidencia actual es calibración/validación controlada, no una prueba de ahorro de billing |

## Arquitectura

~~~text
agente de código
    |
    v
 io_context MCP
    |
    +-- search  -> descubrimiento determinístico
    +-- extract -> proyección exacta y acotada
    +-- query   -> política local / Jev opcional
                    |
                    +-- evidencia focalizada
                    +-- principal
                    +-- worker aprobado
                          |
                          +-- manual  -> modelo fijo del usuario
                          +-- suggest -> solo recomendación
                          +-- auto    -> perfil dinámico acotado
~~~

I/O Delegation no intenta reemplazar al agente principal. Su trabajo es preparar la menor evidencia útil y evitar trabajo de modelo cuando un parser, búsqueda literal, compilador, test o lectura acotada puede resolver la pregunta directamente.

## Soporte por host

| Capacidad | Claude Code | Codex | Cursor |
| --- | --- | --- | --- |
| Gateway MCP global/user | Sí | Sí | Sí |
| search / extract / query | Sí | Sí | Sí |
| Read guard opcional | Sí | Sí | Sí |
| Integración de compactación automática | Sí | Sí | Sí |
| Política host-aware de modelos | Límite principal | Sí | Sí |
| Workers CLI | Paths/adapters existentes | Sí | Sí |
| Calibración live real autenticada | No se afirma | Sí | Todavía no |

La orquestación host-aware de perfiles está enfocada deliberadamente en Codex y Cursor. Claude Code comparte el gateway, read-control y compactación.

## Inicio rápido

~~~bash
git clone https://github.com/pnll1991/io-delegation-skill.git
cd io-delegation-skill
~~~

Windows:

~~~powershell
.\io-delegation.cmd setup --project "D:\ruta\al\proyecto"
~~~

macOS / Linux:

~~~bash
./io-delegation setup --project "/ruta/al/proyecto"
~~~

Preview sin escribir nada:

~~~bash
io-delegation setup --project . --dry-run
~~~

Inspeccionar la instalación efectiva:

~~~bash
io-delegation status --project .
io-delegation doctor --project .
~~~

Setup instala un runtime estable bajo ~/.io-delegation/, da al proyecto una identidad persistente, registra un MCP io_context a nivel de usuario/host y mantiene estado/configuración privada fuera del repositorio sobre el que estás trabajando.

Los proyectos pueden moverse. Volvé a ejecutar setup después de mover un proyecto o cambiar el ejecutable de Python para refrescar metadata de máquina sin cambiar la identidad del proyecto.

## Comportamiento por defecto

Una instalación normal arranca de forma conservadora:

| Setting | Default |
| --- | --- |
| Search/extract determinístico | On |
| Routing local de query | On |
| Selector de ruta experimental Jev | Off |
| Control de modelo | suggest |
| Preset | balanced |
| Cambio dinámico de modelo | Off salvo que se seleccione auto |
| Compactación automática | On |
| Read guard | Off |
| Auto-dispatch semántico legacy | Off |
| Operaciones sensibles | Principal |

Configuración típica:

~~~bash
io-delegation setup --project . --agent all
io-delegation setup --project . --model-mode suggest --model-preset balanced
~~~

Alternativas explícitas:

~~~bash
# Nunca cambiar un modelo configurado explícitamente
io-delegation setup --project . --model-mode manual

# Permitir selección dinámica entre perfiles aprobados
io-delegation setup --project . --model-mode auto --model-preset balanced

# Selector experimental de rutas con Jev
io-delegation setup --project . --jev on

# Escape hatch persistente de orquestación
io-delegation setup --project . --orchestration off

# Read guard opcional
io-delegation setup --project . --guard observe
io-delegation setup --project . --guard enforce

# Deshabilitar/restaurar compactación automática
io-delegation setup --project . --compaction off
io-delegation setup --project . --compaction on
~~~

La mera presencia de TYPESAFE_API_KEY no activa silenciosamente el selector experimental de rutas con Jev.

## Superficie MCP

La interfaz pública es intencionalmente pequeña.

### search

Descubrimiento determinístico dentro del scope aprobado del proyecto. Sirve para búsqueda literal, paths y excerpts acotados.

### extract

Proyecciones exactas sin inferencia. Soporta:

- metadata HTML como title, h1, canonical y description;
- JSON pointers RFC 6901;
- rangos de líneas one-based;
- spans de caracteres normalizados.

Los resultados demasiado grandes se rechazan: no se truncan silenciosamente y luego se tratan como completos.

### query

Es la interfaz semántica de contexto. Recibe fragmentos seleccionados explícitamente más una pregunta/hint de operación y decide entre:

- evidencia focalizada;
- razonamiento principal;
- worker semántico aprobado.

query funciona sin servicios externos. Jev y ejecución de workers son capas opcionales arriba del gateway local.

Ver [referencia Context MCP](skills/io-delegation/references/CONTEXT_MCP.md).

## Control de modelos: manual, suggest, auto

Con un worker CLI aprobado de Codex/Cursor, Jev puede puntuar requisitos de la tarea mientras la decisión de modelo sigue siendo local y editable por el usuario.

### manual

Nunca cambia el modelo seleccionado por el usuario.

Con una configuración explícita codex-cli/cursor-cli conserva exactamente ese modelo. Un host-cli genérico sin modelo fijo vuelve al principal en vez de inventar una selección.

### suggest — default

Calcula un perfil recomendado, pero no cambia dinámicamente de modelo.

Es el default porque mantiene visible el control del modelo mientras la política puede mostrar una recomendación de perfil mínimo suficiente.

### auto — opt-in explícito

Puede elegir y reintentar perfiles aprobados bajo ceilings locales y guards de escalación.

El benchmark live usa auto porque justamente valida el routing dinámico. Eso no cambia el default del producto.

## Política de modelos calibrada

Jev devuelve cinco scores:

- cheap_model_sufficient
- risk_high
- uncertainty_high
- reasoning_required
- parallelism_useful

La fuerza del modelo usa suficiencia de modelo barato, reasoning y risk.

Dos correcciones importantes que salieron de la calibración live:

- uncertainty_high diagnostica contexto faltante/incompleto; por sí solo no compra un modelo más fuerte;
- parallelism_useful describe descomposición, no inteligencia, por lo que tampoco incrementa fuerza de modelo.

Cuando la incertidumbre indica un problema de contexto, la política vuelve al principal en vez de intentar resolver evidencia faltante con un modelo más caro.

Operaciones sensibles — debugging, arquitectura, seguridad, edición y generación — quedan en el principal.

### Ladders default de Codex

| Preset | Orden |
| --- | --- |
| cost | Luna medium → Luna high → Terra medium → Sol medium → Astra low |
| balanced | Luna high → Terra medium → Sol medium → Astra low |
| quality | Luna high → Terra medium → Sol medium → Astra low |

Balanced empieza deliberadamente en Luna high. Luna medium sigue disponible en cost y políticas custom.

### Ladders default de Cursor

| Preset | Orden |
| --- | --- |
| cost | Luna medium → Luna high → Sol medium |
| balanced | Luna medium → Luna high → Sol medium → Sol high |
| quality | Luna high → Sol medium → Sol high |

El registry de Cursor está informado por metadata de CursorBench 4.0 y sigue siendo editable. Estos defaults no se presentan como validación empírica live derivada de los tests de Codex.

### Guards de escalación y Astra

Defaults:

- máximo de escalaciones model-profile: 1;
- preset cost: ratio máximo de salto 2x;
- balanced: 10x;
- quality: 25x;
- fallos de transporte/configuración/budget no recorren el ladder;
- volver al principal no cuenta como model escalation.

Balanced usa 10x porque el caso hard-factual live necesitó el recovery útil Luna-high → Terra-medium; el guard anterior de 4x bloqueaba incorrectamente esa recuperación.

Astra es un tier excepcional, no un fallback normal por incertidumbre. La política Codex default requiere:

- demand >= 0.90;
- reasoning_required >= 0.85;
- y además risk_high >= 0.55 o cheap_model_sufficient <= 0.12.

El selector toma el primer perfil aprobado cuya capacidad declarada cubre la demanda. Ya no usa el objetivo viejo cost/capability que podía favorecer dos veces a modelos caros.

Ver [ORCHESTRATION.md](skills/io-delegation/references/ORCHESTRATION.md).

## Compactación automática guiada por Jev

La compactación es un subsistema separado del selector opcional de rutas Jev.

~~~bash
# Default
io-delegation setup --project . --compaction on

# Escape hatch persistente por proyecto
io-delegation setup --project . --compaction off
~~~

| Host | Integración |
| --- | --- |
| Claude Code | Reemplazo de session.compact usando el core vendorizado fast-jev-compaction; texto user/assistant queda verbatim y evidencia vieja de tools puede podarse/truncarse |
| Codex | Journaling local de prompts/tool I/O; PreCompact ejecuta la decisión y la evidencia retenida se reinyecta después de la compactación nativa |
| Cursor | Usa preCompact más el primer postToolUse/un follow-up stop acotado para reinyectar evidencia, porque el hook de compactación de Cursor es observacional |

Si Jev o la API key no están disponibles, la compactación cae al mecanismo nativo del host en vez de bloquear la sesión.

En el bridge de Codex/Cursor, los bodies completos de tool results quedan locales. Jev recibe metadata de tamaño/error y la evidencia exacta retenida se reinyecta localmente cuando corresponde.

## Read guard opcional

El read guard no es un sandbox de seguridad y está apagado por defecto.

| Modo | Comportamiento |
| --- | --- |
| off / solo instrucciones | Sin interceptar |
| observe | Evalúa lecturas cubiertas y registra metadata sin bloquear violaciones de budget |
| enforce | Deniega lecturas cubiertas que superan el presupuesto y propone alternativas acotadas |

Budget default:

- 350 líneas de source;
- 64.000 bytes por lectura/lote de shell reconocido.

Integraciones cubiertas:

| Host | Hook |
| --- | --- |
| Claude Code | PreToolUse: Read, read_file, Bash |
| Codex | PreToolUse: Read, read_file, Bash |
| Cursor | preToolUse: Read, Shell |

Reconoce un conjunto acotado de lectores literales como cat, head, tail, Get-Content y rangos sed. No interpreta programas shell arbitrarios ni impone un límite de tokens de sesión completa.

Ver [ENFORCEMENT.md](skills/io-delegation/references/ENFORCEMENT.md).

## Filosofía de routing

| Situación | Camino preferido |
| --- | --- |
| Search/parser/test/compiler responde | Tool determinístico |
| Función conocida o región pequeña | Lectura focalizada |
| Lookup factual grande sobre texto seleccionado | query / bulk factual path |
| Archivo nuevo repetitivo con contrato claro | Candidato code-write revisable |
| Debugging, arquitectura, seguridad, pagos, lógica crítica | Principal con evidencia directa |
| Editar código existente | Releer source actual y editar con precisión |
| Contexto faltante | Buscar evidencia o volver al principal; no subir modelo por defecto |

El runner legacy bulk-read/code-write sigue disponible para compatibilidad y uso explícito de worker. code-write produce un candidato bajo .io-delegation/candidates/; no sobreescribe silenciosamente archivos vivos del proyecto.

## Límites de datos y seguridad

I/O Delegation separa tres paths relacionados con Jev porque tienen límites de datos diferentes.

| Feature | Datos enviados a TypeSafe/Jev | Límite importante |
| --- | --- | --- |
| Selección de ruta | Texto de tarea + metadata agregada | Sin source bodies ni file names |
| Scoring de requisitos de modelo | Texto de tarea + metadata agregada | Model IDs/providers se eligen localmente |
| Compactación | Contexto más amplio de conversación/tool input | En Codex/Cursor los tool-result bodies quedan locales; puede enviarse metadata de tamaño/error |

Otras propiedades:

- credenciales en variables de entorno;
- setup guarda nombres de variables, no valores plaintext;
- configs privadas de worker van fuera del proyecto;
- runtime, estado y auditoría viven bajo ~/.io-delegation/;
- las configuraciones de host se backupean antes de cambios administrados;
- restore no pisa cambios posteriores del usuario salvo --force explícitamente revisado;
- la validación live de Codex en self-hosted runner es owner-only y hace checkout de main confiable;
- el test TypeSafe live usa handoff cifrado y no persiste intencionalmente la key plaintext.

El gateway no es un sandbox, DLP ni bypass de permisos. Los sandboxes, approvals y trust boundaries del host siguen aplicando.

## Benchmarks y evidencia

No existe un único claim de "X% de ahorro". El repo conserva resultados favorables y negativos porque cada experimento mide una capa distinta.

### Resumen de evidencia

| Evidencia | Muestra | Observación principal | Decisión de producto |
| --- | ---: | --- | --- |
| Census independiente 0.1 | 5 casos | Los casos grandes con skill cold usaron 64,03–72,00% menos tokens o200k que whole-file; controles small/already-focused empeoraron fuerte | Usar contexto focalizado; bypass para tareas pequeñas/ya focalizadas |
| Piloto local Qwen 0.1 | 9 respuestas + control forced-worker | Bajó consumo en casos grandes, pero el quality gate estricto pasó solo 1/9; forced worker consumió 35,55% más tokens totales que whole-file | No afirmar ahorro quality-passing; determinístico/focalizado primero |
| Activation A/B 2026-09-17 | 15 pares | Mejora histórica aparente | Invalidado por herencia de config Codex del usuario; no usar como evidencia actual |
| Jev A/B 2026-09-18 | 20 pares / 40 runs | Ambos arms 11/20; Jev se llamó en 9 pares; sin regresiones causales de calidad en el subset llamado | Selector de ruta Jev sigue opt-in |
| Validación semantic worker | 25 pares | ~97% overhead mediano de tokens principal+worker, ~16,3s de overhead mediano, 2/25 workers aceptados | Auto-dispatch semántico incondicional sigue off |
| Calibración live de modelos Codex 2026-09-20 | 3 casos + probes directos | Easy/medium quedaron en Luna-high; hard factual recuperó con un único Luna-high → Terra-medium; ningún caso de calibración necesitó Astra | suggest sigue default; auto queda acotado por política calibrada |

### Census de tokens 0.1

Con tiktoken 0.11.0 sobre message JSON serializado:

| Caso | o200k whole | o200k focused | o200k cold skill | Reducción cold vs whole |
| --- | ---: | ---: | ---: | ---: |
| Constants | 6.854 | 203 | 1.919 | 72,00% |
| Defaults | 6.862 | 752 | 2.468 | 64,03% |
| Candidate fields | 6.861 | 576 | 2.292 | 66,59% |
| Small installer | 772 | 638 | 2.354 | -204,92% |
| Already focused | 203 | 203 | 1.919 | -845,32% |

Son conteos BPE de una serialización controlada, no billing del proveedor ni mediciones de calidad.

Evidencia: [2026-09-07-census.json](benchmarks/results/2026-09-07-census.json).

### Piloto local 0.1

El replay controlado con Qwen/Qwen2.5-Coder-1.5B-Instruct midió menor consumo de tokens en lookups grandes, pero el quality gate predeclarado de JSON puro pasó solo 1 de 9 respuestas principales.

El control forced-worker consumió:

- 7.224 tokens del worker;
- 2.132 tokens del fallback/main;
- 9.356 tokens combinados;
- contra 6.902 del baseline whole-file;
- 35,55% más consumo total.

Ese resultado negativo es una de las razones por las que I/O Delegation no trata una llamada a worker como automáticamente más barata.

Evidencia: [2026-09-07-live.json](benchmarks/results/2026-09-07-live.json) y [metodología](benchmarks/README.md).

### Jev A/B — 2026-09-18

Muestra aislada completa:

- 40 runs;
- 20 tareas pareadas;
- gateway-local: 11/20 successes;
- gateway-jev: 11/20 successes;
- Jev realmente se llamó en 9/20 pares;
- cinco pares válidos donde Jev fue llamado tuvieron mediana de delta de tokens principal de -23,29% y mediana de wall-time de -27,61%;
- esos cinco pares eran todos de la familia targeted;
- una mejora causal de calidad;
- cero regresiones causales de calidad en pares donde Jev realmente fue llamado;
- siete effective-route mismatches en el arm Jev total;
- la familia multi-file nunca llegó a Jev.

El subset llamado es prometedor pero demasiado angosto para justificar activación automática. Por eso el route selector sigue siendo opt-in explícito.

Evidencia: [jev-ab-20260918.json](benchmarks/v1-validation/evidence/jev-ab-20260918.json).

### Resultado de activation invalidado

El artifact del 2026-09-17 se conserva intencionalmente, pero está marcado invalidated: el runner heredó configuración Codex del usuario y no fijó el fallback de sandbox Windows soportado. Sus porcentajes headline no deben usarse como evidencia actual.

Evidencia: [activation-20260917.json](benchmarks/v1-validation/evidence/activation-20260917.json).

### Calibración live de política Codex — 2026-09-20

Es una validación real usando Codex CLI autenticado en el self-hosted Windows runner del usuario con TypeSafe/Jev disponible. El corpus es sintético y sanitizado; no contiene source privado.

Fixture de calibración commiteado:

[model-policy-live-calibration-20260920.json](benchmarks/v1-validation/evidence/model-policy-live-calibration-20260920.json)

Último rerun seguro post-merge:

[GitHub Actions run 35515936634](https://github.com/pnll1991/io-delegation-skill/actions/runs/35515936634) sobre commit 7a911d8df89e857777b236d0749d1bb533d096c2.

Routing observado:

| Caso | Jev raw tokens | Inicial | Final | Model calls | Escalaciones | Worker raw tokens | Wall time |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| easy | 861 | Luna high | Luna high | 1 | 0 | 12.095 | 28,0s |
| medium | 876 | Luna high | Luna high | 1 | 0 | 12.270 | 21,9s |
| hard factual | 895 | Luna high | Terra medium | 2 | 1 | 26.997 | 72,0s |

Política efectiva del benchmark:

- mode: auto;
- preset: balanced;
- order: Luna high → Terra medium → Sol medium → Astra low;
- max model escalations: 1;
- max escalation cost ratio: 10x.

El caso hard-factual es la calibración clave: Luna high falló el validator local de evidencia, un retry a Terra medium pasó y el ladder se detuvo ahí.

Los probes directos one-shot siguen mostrando ruido. En el último rerun Luna high, Terra medium y Astra low pasaron; Sol medium falló el literal-evidence validator. Runs anteriores dieron combinaciones diferentes. Por eso la política se calibra sobre invariantes y recovery acotado, no sobre la idea de que un modelo específico siempre pasa.

Esta evidencia live de Codex no demuestra ahorro en producción y no valida empíricamente Cursor.

## Tests y validación

Suite offline:

~~~bash
python -m unittest discover -s tests -v
~~~

CI corre en:

- Ubuntu;
- Windows;
- macOS;
- Python 3.10;
- Python 3.13;
- tests/typechecks de compactación;
- checks de token census.

La calibración de model policy también está congelada en regression tests para que los patrones históricos de scores Jev sigan:

- arrancando Codex balanced en Luna high;
- evitando Astra en casos ordinarios de calibración;
- permitiendo un solo recovery Luna-high → Terra-medium;
- evitando recorrer todo el ladder.

Los tests automáticos no reemplazan validación real autenticada. El repo separa explícitamente unit/protocol coverage de evidencia live por host.

## Configuración de workers

Las configuraciones reales y credenciales deben quedar fuera del proyecto.

Punto de partida:

skills/io-delegation/assets/worker.host-cli.example.json

Instalar explícitamente:

~~~bash
io-delegation setup --project . --worker-config /ruta/privada/worker.host-cli.json
~~~

Desactivar worker:

~~~bash
io-delegation setup --project . --no-worker
~~~

Usuarios avanzados pueden reemplazar registries/orden, bloquear perfiles, bajar ceilings, cambiar límites de escalación y — solo en Cursor — configurar explícitamente un native-router-first con model string revisado. I/O Delegation nunca inventa model IDs.

## Lifecycle

Eliminar solo integración administrada:

~~~bash
io-delegation remove --project . --dry-run
io-delegation remove --project .
~~~

Ver backups:

~~~bash
io-delegation backups
~~~

Restore seguro:

~~~bash
io-delegation restore BACKUP_ID --dry-run
io-delegation restore BACKUP_ID
~~~

Restore se niega a pisar configuración de host modificada después de I/O Delegation salvo que --force sea revisado explícitamente.

## Mapa del repositorio

~~~text
io_gateway.py
  setup / status / doctor / remove / backups / restore

skills/io-delegation/
  SKILL.md
  scripts/
    context_mcp.py
    context_bootstrap.py
    context_query.py
    context_orchestrator.py
    model_policy.py
    context_telemetry.py
    read_guard.py
    io_delegate.py
  references/
    CONTEXT_MCP.md
    ORCHESTRATION.md
    ENFORCEMENT.md
    ADAPTERS.md
    WORKERS.md
    VALIDATION.md
  assets/
    worker.host-cli.example.json

scripts/
  codex_live_smoke.ps1
  codex_live_keygen.ps1
  codex_live_ab.py
  codex_live_ab_with_key.ps1
  codex_live_ab_from_comment.ps1

benchmarks/
  README.md
  results/
  v1-validation/evidence/

tests/
docs/
.github/workflows/
~~~

## Límites actuales

I/O Delegation sigue siendo software beta guiado por evidencia.

No se debe inferir más de lo que prueban los experimentos:

- no hay claim fijo de porcentaje de ahorro de tokens;
- no hay claim de ahorro de billing del proveedor;
- no se afirma que delegar automáticamente a workers sea siempre más barato;
- no se afirma que Cursor esté calibrado live a partir de evidencia Codex;
- el read guard no es un sandbox de seguridad;
- instalar un hook no prueba que un cliente realmente lo haya ejecutado;
- TypeSafe/Jev no es obligatorio para que el gateway sea útil.

La validación de mayor valor que falta incluye una muestra pareada real más grande con accounting completo principal + worker + Jev y paridad de lifecycle autenticada en Cursor/Claude.

## Documentación

- [Diseño de producto](docs/PRODUCT_V1.md)
- [Instalación](docs/INSTALLATION_V1.md)
- [Context Gateway / MCP](skills/io-delegation/references/CONTEXT_MCP.md)
- [Orquestación host-aware](skills/io-delegation/references/ORCHESTRATION.md)
- [Workers](skills/io-delegation/references/WORKERS.md)
- [Adapters](skills/io-delegation/references/ADAPTERS.md)
- [Read enforcement](skills/io-delegation/references/ENFORCEMENT.md)
- [Validación](skills/io-delegation/references/VALIDATION.md)
- [Metodología de benchmark](benchmarks/README.md)
- [Changelog](CHANGELOG.md)

## Licencia

MIT. Ver [LICENSE](LICENSE).
