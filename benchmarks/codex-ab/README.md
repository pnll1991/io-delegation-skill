# Codex × io-delegation — A/B real

Este benchmark separa efectos que suelen mezclarse:

1. `baseline`: Codex sin la skill.
2. `skill-only`: mismo modelo/tarea, con `io-delegation` instalada y activada.
3. `skill-hooks`: skill + `PreToolUse` read guard, sin necesitar otro modelo.
4. `skill-hooks-worker`: experimento opcional con un worker aprobado.

La métrica principal es **CPTS — cost per successful task**. Un ahorro de tokens con menor tasa de éxito no cuenta como mejora.

## Qué mide

`codex_io_ab.py` ejecuta `codex exec --json` y registra input, cached input, cache-write input, output, reasoning output, tools, comandos, MCP, subagentes, tiempo y acceptance/regression tests.

También inspecciona la salida de comandos. Cuando `io_delegate.py` emite un evento `worker_response`, suma worker calls, worker input/output tokens y request bytes. Así no se declara “menos tokens” mirando sólo al agente principal mientras un worker consume por otro lado.

## Aislamiento

Cada tarea/estrategia/repetición corre en un `git worktree --detach` nuevo. La skill y los hooks experimentales se instalan sólo en el worktree del brazo correspondiente y se commitean antes de medir el patch.

`forbid_base_paths` aborta si el commit base ya contiene `.agents/skills/io-delegation`, evitando contaminar el baseline. El orden de las corridas se randomiza con una seed reproducible.

## Piloto sin worker

Copiá `manifest.example.json` y cambiá:

- `repo`
- `variables.io_delegation_root`
- el SHA base de cada tarea
- prompt real
- acceptance real
- regression real

No cambies modelo, reasoning effort o sandbox entre estrategias. Si fijás un modelo, usá exactamente el mismo en las tres.

```bash
python benchmarks/codex-ab/codex_io_ab.py my-manifest.json \
  --task large-read-bug \
  --output results/pilot
```

Para aislar hooks:

```bash
python benchmarks/codex-ab/codex_io_ab.py my-manifest.json \
  --strategy baseline --strategy skill-hooks \
  --task large-read-bug \
  --output results/pilot-hooks
```

Salida:

- `runs.csv`
- `runs.json`
- `aggregate.json`
- `summary.md`
- `runs/<run>/codex.jsonl`
- `runs/<run>/patch.diff`
- `runs/<run>/acceptance.json`
- `runs/<run>/regression.json`
- `runs/<run>/setup.json`

## Worker opcional

`manifest.worker.example.json` instala skill + hooks y copia una configuración de worker aprobada al worktree. `worker_config.example.json` viene con `approved: false`; revisalo antes de habilitarlo.

Para calcular costo de sistema completo completá los ratios de precio del worker respecto del precio de input del modelo principal:

```json
"worker_input_price_to_main_input": 0.0,
"worker_output_price_to_main_input": 0.0
```

Si quedan en `null`, o una llamada no reporta usage, el benchmark deja `system_CPTS` como desconocido en lugar de inventarlo.

## Interpretación

Prioridad:

1. misma o mejor tasa de éxito
2. menor `system_CPTS` cuando está disponible
3. menor `principal_CPTS`
4. menos tiempo/rework

Para adoptar `skill-hooks`, exigir al menos que el éxito no baje y `principal_CPTS` mejore. Para `skill-hooks-worker`, además exigir `system_cost_complete = true` y `system_CPTS < baseline`.

## Estado de validación

El harness fue probado end-to-end con un Codex simulado: setup por estrategia, worktrees, JSONL, worker usage, acceptance/regression, agregación y resumen. Eso valida el harness, **no demuestra todavía un porcentaje de ahorro real**. El número real requiere ejecutarlo donde `codex` esté instalado y autenticado.
