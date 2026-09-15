# Workers configurados y medibles

Esta integración conecta `io_delegate.py` con un auxiliar real. Una skill instalada o un hook que bloquea una lectura NO configuran un modelo por sí solos.

## Inicio en Windows con la sesión de Codex existente

Desde la rama `codex-io-delegation-ab`:

```powershell
cd D:\io-delegation-skill-ab
git pull --ff-only
python benchmarks\codex-ab\setup_worker.py --repo D:\landing-kuatrometric --adapter codex-cli --worker-model gpt-5.6-luna --approve-worker
```

`--approve-worker` autoriza el adaptador elegido y UNA consulta real con un archivo sintético. Consume cuota del modelo. El script no envía el proyecto durante esa prueba, no copia credenciales, no cambia configuración global y no crea cuentas. Primero comprueba versión, flags y estado de autenticación de la CLI. Un fallo detiene el proceso antes del A/B.

El script crea una carpeta nueva bajo `benchmarks/codex-ab/local-runs/` con configuración local, logs, `worker-smoke.json` y, solo si pasó, `manifest.worker.json`. Imprime el comando exacto para ejecutar la comparación. `--run` permite ejecutar esa comparación después de que pase la prueba; por defecto son dos tareas adicionales (baseline y worker obligatorio), una repetición cada una. Nada borra los resultados anteriores.

El identificador de modelo es un parámetro. El ejemplo usa el que funcionó en el equipo del usuario; no garantiza disponibilidad universal. Un worker con el MISMO modelo no es necesariamente más barato. Usar esfuerzo `low` tampoco garantiza ahorro ni calidad. Primero se comprueba la integración; después se mide eficiencia.

## Qué comprueba la prueba previa

1. La CLI reconoce los flags necesarios y existe autenticación utilizable.
2. Una llamada al worker devuelve un valor aleatorio de un archivo sintético, con evidencia literal.
3. El contrato, la cobertura y el hash del archivo son válidos.
4. Se registró una invocación y una respuesta aceptada, con input/output observables.

`worker-smoke.json` vincula el resultado al hash de la configuración y al código del ejecutor. Si cambian, el benchmark exige repetir únicamente la prueba previa. No es una certificación de seguridad ni de calidad general.

## Aislamiento y límites

`codex-cli` usa una conversación nueva, efímera, con directorio temporal; recibe el corpus por stdin, no la conversación del principal ni el repositorio como cwd. Reutiliza el login donde ya existe. Usa `read-only`, aprobación `never`, sin búsqueda web, shell ni multiagente habilitados por este adaptador. Rechaza resultados si el stream muestra herramientas. No se pasan flags YOLO ni se omite la confianza de hooks.

Esto no equivale a una sandbox universal ni a borrar todo el contexto administrado por el cliente. Herramientas, configuración administrada, autenticación y políticas del proveedor siguen dependiendo de la instalación. Los flags se contrastaron con documentación oficial; la versión real del usuario se verifica mediante `--help` antes de inferencia.

No hay fallback de proveedor ni reintentos automáticos. El límite de llamadas del ejemplo es cuatro por workspace y solo se ejecuta un auxiliar a la vez. Un lock dejado por una interrupción bloquea nuevas llamadas hasta revisión; no se borra a ciegas.

## Otros transportes

Siguen disponibles `command` y `chat-completions`. El segundo admite un servidor loopback existente, por ejemplo LM Studio, pero no instala ni carga modelos. Para usarlo hay que elegir `--adapter chat-completions`, el `--worker-model` real y el endpoint correcto. Los destinos remotos requieren un `--config` explícitamente aprobado con HTTPS y `allow_remote: true`. Las claves se leen de la variable de entorno declarada, nunca se embeben en comandos ni se publican.

## Registro que no depende de un recorte de la terminal

Cada llamada escribe metadatos en `.io-delegation/worker-events.jsonl`, incluso si el modelo falla después. No se registran código fuente, prompts, rutas de corpus, comandos ni credenciales. Los eventos llevan `call_id` y secuencia para deduplicar:

`worker_attempt → worker_dispatched → worker_response → worker_completed`

Los errores terminan con `worker_error`. Un intento rechazado antes de invocar tiene cero llamadas. Un timeout después de invocar tiene consumo desconocido salvo que el proveedor haya reportado usage. Respuestas rechazadas por evidencia, JSON o truncamiento cuentan en el consumo conocido. No se convierten en gratis.

El contador `worker_calls` ahora cuenta despachos, no solamente respuestas. `worker_attempts`, `worker_responses`, `worker_accepted`, `worker_failures` y `worker_usage_unknown_calls` distinguen las etapas. Los subagentes nativos no son estos workers; si aparecen y no se puede atribuir su consumo, el costo del sistema queda incompleto.

## No confundir pruebas

- `required`: prueba de integración; exige al menos un `bulk-read` aceptado. Es deliberadamente forzada, NO evidencia de activación económicamente óptima.
- `optional`: prueba de enrutamiento; el agente puede usar un parser o lectura breve sin delegar. Cero llamadas puede ser correcto.
- `disabled`: control sin auxiliares configurados por el benchmark.

El A/B con worker no instala hooks por defecto: primero separa la conexión al auxiliar de la confianza de hooks. Los brazos anteriores con hooks siguen disponibles; sin registros de ejecución del anfitrión se marcan como no comparables para esa intervención. Un registro del hook no certifica cobertura de todas las herramientas o lecturas.

## Referencias consultadas

- Codex no interactivo: https://learn.chatgpt.com/docs/non-interactive-mode
- Configuración: https://learn.chatgpt.com/docs/config-file/config-reference
- Hooks y confianza: https://learn.chatgpt.com/docs/hooks

Pruebas offline usan stubs y procesos reales, no facturación ni sesiones autenticadas. La prueba real debe ejecutarse en el equipo donde está Codex.
