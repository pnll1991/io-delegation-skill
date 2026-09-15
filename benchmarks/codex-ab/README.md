# Codex x io-delegation: integración y benchmark corregidos

La medición anterior de Kuatrometric instalaba la skill y opcionalmente hooks, **sin conectar un worker**. Los resultados históricos no se convierten en resultados de delegación ni se sobrescriben. El nuevo ejecutor separa conexión, decisión de delegar, calidad y consumo.

## Un comando de preparación

```powershell
cd D:\io-delegation-skill-ab
git pull --ff-only
python benchmarks\codex-ab\setup_worker.py --repo D:\landing-kuatrometric --adapter codex-cli --worker-model gpt-5.6-luna --approve-worker
```

Reutiliza el login existente de Codex. Hace una llamada real pequeña con datos sintéticos, valida respuesta/evidencia/usage y genera un manifiesto local completo. No inicia el A/B si falla. Después imprime el comando exacto para dos corridas: baseline y `skill-worker-required`. Para ejecutar ambas fases seguidas se puede agregar `--run`; usa cuota. No se requieren otra API ni edición manual de JSON.

Los resultados van a una carpeta nueva con fecha e identificador, nunca a una que se borra antes. `--repetitions 3` amplía a seis corridas; no hacerlo antes de confirmar el funcionamiento. `--worker-mode optional` mide la decisión libre de delegar, en vez de forzarla.

El worker usa el mismo modelo del ejemplo, con esfuerzo bajo. **No se presupone que eso sea más barato.** El modo obligatorio demuestra conexión; la tarea HTML puede seguir resolviéndose mejor con un parser local.

[Configuración, aislamiento, estados y otros transportes](../../skills/io-delegation/references/WORKERS.md).

## Qué cambió

- Adaptador `codex-cli`, además de `command` y `chat-completions`.
- Prueba previa sintética y recibo ligado al hash del código/configuración.
- Worker instalado y configuración copiada explícitamente al worktree en brazos con worker.
- Intentos, despachos, respuestas, rechazos, errores y consumo desconocido separados.
- Metadatos durables; deduplicación por llamada/secuencia, no dependencia del output truncado de terminal.
- Sin precios inventados: tokens brutos y, solo con tarifas explícitas, estimación USD separada.
- Manifest/output con UTF-8 BOM, argv nativos Windows, diagnósticos compactos, snapshots y capturas.
- SHA inicial fijado una sola vez y resultados existentes protegidos.
- Hooks instalados no equivalen a hooks observados; no se omite su confianza.
- Lectura de búsquedas acotada mediante `bounded_search.py` para evitar líneas minificadas gigantes.

## Datos emitidos

`runs.json`, `runs.csv`, `aggregate.json`, `summary.md`, `manifest.snapshot.json`, `environment.json`. Cada corrida conserva `codex.jsonl`, stderr, comandos, prompt, aceptación/regresión, estado Git, diff, archivos declarados y eventos de worker/hook cuando existan.

Los logs de Codex pueden contener código del proyecto. No se publican automáticamente ni se suben a GitHub. La telemetría propia del worker contiene solo metadatos.

### Métricas

`principal_token_CPTS = suma input + output del principal / éxitos`

`system_token_CPTS = suma input + output del principal y workers / éxitos`

Cached input es parte del input, no una suma adicional. Reasoning es parte del output. Una corrida sin usage no vale cero; un timeout con gasto desconocido invalida la atribución completa. Los intentos fallidos conocidos sí entran al numerador. `comparison_valid` exige el contrato de la intervención, además de calidad y observabilidad. No extrapolar una tarea ni tres repeticiones a todos los repositorios.

Los antiguos pesos de caché 0,1 y escritura 1,25 **ya no son supuestos del harness**. Para estimación monetaria se puede incluir `pricing` indexado por el modelo real con `input_per_million`, `cached_input_per_million`, `cache_write_per_million`, `output_per_million`. Si hay escrituras de caché no nulas, declarar `cache_write_accounting: "exclusive_subset_of_input"` solamente si ese proveedor reporta categorías exclusivas. Sin datos completos no se calcula USD. Estas tarifas no convierten tokens en consumo exacto de un plan ChatGPT.

## Ejecución manual y preflight sin modelos

```powershell
python benchmarks\codex-ab\codex_io_ab.py RUTA_MANIFEST_GENERADO --preflight
python benchmarks\codex-ab\codex_io_ab.py RUTA_MANIFEST_GENERADO --output RUTA_NUEVA
```

Los manifiestos `manifest.example.json` y `manifest.worker.example.json` son plantillas, no configuraciones operativas. Para Kuatrometric con worker usar el generador; el viejo `manifest.kuatrometric.example.json` sigue siendo un control histórico sin worker.

## Validación del código

```powershell
python -m unittest discover -s tests -p "test_worker*.py" -v
python -m unittest discover -s tests -p "test_codex_worker*.py" -v
```

Los tests usan stubs, servidores locales de prueba y subprocesses. Las pruebas de CLI con ejecutable POSIX sintético se omiten en Windows; no afirman integración autenticada en Windows. No sustituyen la prueba sintética real en el equipo del usuario.
