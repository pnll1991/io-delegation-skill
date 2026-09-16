# Codex x io-delegation: worker por MCP y prueba del recorrido completo

La prueba directa de un worker no garantiza que se pueda iniciar desde el shell de un agente restringido. El diagnóstico de Windows mostró una llamada a `io_delegate.py` que no llegaba a iniciar Python (`Access is denied`), mientras el inventario y la regresión sí pasaban. No se reclasifican esas corridas como delegación válida ni se sobrescriben sus resultados.

## Continuar desde un smoke directo ya aprobado

Conservá el `manifest.worker.json`, `worker.local.json` y `worker-smoke.json` existentes. No repitas `setup_worker.py` si sus hashes siguen vigentes.

```powershell
cd D:\io-delegation-skill-ab
git pull --ff-only
python benchmarks\codex-ab\verify_worker_mcp.py "RUTA_COMPLETA_AL_MANIFEST.worker.json"
```

El verificador primero inicia el servidor MCP y comprueba el protocolo sin modelos. Después ejecuta una prueba sintética del recorrido **Codex principal -> herramienta MCP bulk_read -> worker -> evidencia y uso registrados**. Usa un turno pequeño del principal y una llamada prevista al auxiliar. No inicia el benchmark automáticamente. `--offline` comprueba solamente el protocolo y no produce un recibo de validación real.

Si pasa, guarda `boundary-receipt.json` y un nuevo `manifest.mcp.json`, e imprime el comando exacto para comparar baseline y worker una vez. Si falla, conserva los registros y se detiene sin iniciar el A/B. El recibo verifica los hashes del adaptador, el motor y el puente MCP; una prueba directa antigua por sí sola ya no habilita el A/B por MCP.

## Instalación nueva

```powershell
python benchmarks\codex-ab\setup_worker.py --repo D:\landing-kuatrometric --adapter codex-cli --worker-model gpt-5.6-luna --approve-worker
```

Ese comando conserva el login existente y hace una prueba directa con datos sintéticos. Tomá la ruta del manifiesto que genere y pasala a `verify_worker_mcp.py` antes de ejecutar el A/B. No uses `setup_worker.py --run` para saltar la nueva prueba: el harness lo rechaza antes del A/B. Para encadenar las fases después del smoke directo, `verify_worker_mcp.py MANIFEST --run` ejecuta el A/B únicamente cuando pasa la prueba del recorrido MCP; ambas fases consumen cuota.

El modelo de ejemplo del auxiliar sigue siendo Luna. No se presupone que sea más barato por usar esfuerzo bajo. Una tarea de extracción determinista puede seguir siendo más eficiente sin delegación.

## Alcance y permisos

El cliente Codex inicia el servidor MCP local como herramienta configurada para esa invocación. Ya no se le pide al shell restringido que ejecute el Python del perfil del usuario. El principal mantiene `workspace-write`; el worker Codex conserva `read-only`. No se agregan YOLO, cambios de ACL, copias de credenciales, cambios de proveedor ni configuración global.

Un servidor MCP es una integración local de confianza, no una extensión de la sandbox del shell. Por eso el puente expone **solo bulk_read**, sin herramienta de comandos arbitrarios. El modelo recibe únicamente parámetros `paths` y `question`: no elige ejecutable, proveedor, configuración, directorio raíz ni argumentos de shell. El código del servidor y su configuración se cargan desde fuera del worktree; no se ejecuta el código enviado por el agente.

Para el inventario Kuatrometric, el alcance predeterminado es `herramientas/`. Otras tareas deben declarar `worker_allow_prefixes` o `worker_allow_files`. Se rechazan rutas externas, enlaces, junctions, secretos conocidos y entradas que exceden presupuestos. Máximo 12 archivos por llamada; conviene empezar con 1-4. Los 22 archivos del inventario no caben en una sola llamada. El límite del worker es de hasta cuatro despachos por corrida. Los controles de ruta no constituyen una sandbox de propósito general frente a procesos locales hostiles.

La configuración aprobada y la telemetría permanecen fuera del worktree. Los resultados de la herramienta contienen hechos acotados, evidencia literal y hashes. No se vuelca el corpus completo en el contexto principal por rutina. Los errores de permisos no autorizan a bajar las restricciones.

## Resultados y fallos

El harness conserva JSONL, prompts, comandos, aceptación/regresión, estado Git, artefactos declarados y eventos durables. `transport-diagnostic.json` distingue errores al iniciar Python de llamadas MCP observadas. `task_success` expresa calidad de la tarea; `worker_contract_ok` expresa que se cumplió la delegación requerida. Una tarea correcta sin worker no pasa una prueba de delegación obligatoria.

Si falta una llamada aceptada en una variante obligatoria, se guardan los resultados y se cancelan las siguientes corridas. Las instrucciones también exigen detenerse ante un fallo del worker; eso no garantiza que un modelo obedezca durante un turno. La prueba previa y `required=true` para el servidor detectan fallos de conexión antes del benchmark.

Los logs de Codex pueden contener código del proyecto. No se publican ni se suben a GitHub automáticamente. No borres las mediciones anteriores.

## Métricas

`principal_token_CPTS = suma input + output del principal / éxitos`

`system_token_CPTS = suma input + output del principal y workers / éxitos`

Cached input es parte del input; reasoning es parte del output. No se suman dos veces. Uso desconocido no equivale a cero. Las llamadas fallidas con uso conocido se contabilizan. No hay pesos de precio implícitos: USD requiere tarifas explícitas y contabilidad completa. Los tokens tampoco convierten directamente el consumo de una suscripción en dinero.

El ensayo mide esta tarea y la configuración actual del usuario, no un Codex aislado de todas las instrucciones globales ni una garantía universal de ahorro. No atribuir causalidad a hooks no observados. El modo `required` prueba integración; el modo `optional` puede estudiar la decisión de delegar.

## Pruebas

```powershell
python -m unittest discover -s tests -p "test_worker_mcp.py" -v
```

Las 30 pruebas nuevas pasan en Linux con procesos reales y modelos simulados: MCP STDIO, contratos/evidencia, límites, rechazo de rutas, contabilidad, recibos y un A/B completo simulado. Tres pruebas del harness usan un ejecutable POSIX falso y se omiten en Windows; las pruebas de enlaces se omiten cuando el sistema no permite crearlos, y el test TOML requiere Python 3.11. Esto no demuestra ejecución autenticada de MCP en Windows: esa validación es precisamente `verify_worker_mcp.py` en el equipo del usuario.

Fuentes técnicas: documentación oficial de [MCP en Codex](https://developers.openai.com/codex/mcp), [configuración](https://developers.openai.com/codex/config-reference/) y [sandbox de Windows](https://openai.com/index/building-codex-windows-sandbox/). El puente usa MCP STDIO con JSON-RPC delimitado por saltos de línea.
