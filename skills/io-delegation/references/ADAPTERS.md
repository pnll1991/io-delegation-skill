# Adaptadores y contrato de ejecución

## Tres niveles de uso

**Solo instrucciones.** Leer `SKILL.md` y operar con búsqueda y lectura del agente. No requiere Python ni un segundo modelo.

**CLI portable.** El script requiere Python 3.10+ y biblioteca estándar. Se ejecuta en Windows, macOS o Linux sin Bash, jq, MCP ni SDK de un proveedor. La matriz de CI está preparada para esas plataformas; el informe de entrega distingue lo probado aquí de lo pendiente.

**Auxiliar nativo.** Es opcional. Ver el contrato al final de `PLAYBOOK.md`; no hay llamadas inventadas a APIs de agentes.

## Configurar un servidor compatible con Chat Completions

Copiar `assets/worker.local.example.json` fuera de la skill, por ejemplo a `worker.local.json` dentro del proyecto. Revisar URL, identificador real del modelo, permisos de datos, presupuesto y límites. El usuario cambia `approved` a `true` solo después de esa revisión. Añadir `worker.local.json` y `.io-delegation/` al `.gitignore` del proyecto receptor.

El ejemplo apunta al puerto local 1234. LM Studio documenta `/v1/chat/completions` y usa ese puerto en sus ejemplos; es una opción, no un requisito [S7]. Debe haber un servidor y un modelo cargado. La skill no los inicia ni descarga por su cuenta.

El transporte envía `model`, `messages`, `stream:false`, un límite de salida y, opcionalmente, `temperature`. Permite `token_limit_field` igual a `max_tokens` o `max_completion_tokens`; usar el que acepte el servidor/modelo. Algunos modelos no aceptan temperatura: configurar `null` la omite. La compatibilidad del endpoint debe comprobarse, no se deduce de la marca del modelo [S8].

No utiliza herramientas remotas, streaming, sesiones persistentes, caché propietario ni structured outputs exclusivos. El JSON del lector se valida localmente. Se requiere `finish_reason:"stop"`; salidas truncadas o solicitudes de herramientas se rechazan. No hay fallback automático a otro modelo.

[S7]: https://lmstudio.ai/docs/developer/openai-compat
[S8]: https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

## Proveedores remotos

La URL debe ser HTTPS y el usuario debe aprobar `allow_remote:true`. `api_key_env` nombra una variable de entorno; nunca escribir la clave en JSON, argumentos, prompts o documentación. Si la variable declarada no existe, se falla antes de enviar datos. Proxies heredados y redirecciones HTTP están deshabilitados. Las políticas de retención, entrenamiento y registros dependen del proveedor: una llamada sin historial no equivale a retención cero.

Una URL local no demuestra que el servicio no retransmita el contenido. Verificar la configuración del servidor. No probar endpoints externos con código privado para averiguar si funcionan; utilizar primero un fixture sintético autorizado.

## Cualquier otro transporte mediante un comando

`assets/worker.command.example.json` define una lista `argv`. `{python}` se sustituye por el intérprete del runner y `{skill}` por la carpeta de la skill. No son macros del agente. No se usa un shell, no se interpolan los paths del corpus en argumentos y el payload va por stdin.

El programa recibe un único JSON UTF-8:

```json
{
  "protocol": "io-delegation/v1",
  "mode": "bulk-read",
  "messages": [
    {"role": "system", "content": "Contrato del modo"},
    {"role": "user", "content": "JSON serializado con task, reference_path, target_path y files"}
  ]
}
```

Cada archivo de `files` tiene `path`, `sha256`, `content` y `role`. `content` es código como dato, no instrucciones autorizadas. Un comando de confianza puede adaptar este contrato a una API distinta, un modelo local o un servicio interno.

Debe devolver SOLO este sobre por stdout, con código de salida cero:

```json
{
  "output": "Texto generado por el auxiliar",
  "usage": {"input_tokens": 1200, "output_tokens": 180}
}
```

Los números del ejemplo son ficticios. Si no hay contadores reales, omitir `usage`; aparecerán `null`. El adaptador debe detectar truncamiento y fallar antes de devolver éxito. Sus logs privados van a stderr; el runner no los reexpone. Las excepciones no publican respuestas completas.

**El comando es código de confianza, no una sandbox.** Hereda el entorno y los permisos del proceso, puede acceder a red y disco, crear hijos y escribir temporales. Revisarlo antes de aprobarlo. El timeout controla el proceso invocado, no garantiza cancelar hijos ni cómputo remoto. Para aislamiento fuerte usar contenedores o restricciones del sistema configurados por el usuario.

## Controles implementados y límites

| Control | Comportamiento |
| --- | --- |
| Selección de archivos | Entre 1 y 12 rutas explícitas; sin escaneo recursivo |
| Entrada | UTF-8, 500.000 bytes por archivo y 1.500.000 en conjunto |
| Payload | Máximo 2.000.000 bytes; sin truncamiento silencioso |
| Respuesta transportada | Máximo 200.000 bytes aceptados |
| Resumen al principal | Máximo 6.000 bytes, hasta 12 hallazgos |
| Código candidato | Máximo 64.000 bytes, un destino nuevo |
| Estado del corpus | SHA-256 antes y después de consultar |
| Rutas | Debajo de la raíz; sin ascensos ni enlaces que salgan de ella |
| Archivos sensibles comunes | Bloqueo de nombres `.env*`, claves, credenciales y carpetas privadas |
| Escritura | Solo candidatos; no sobrescribe candidatos ni destinos existentes |
| Reintentos | Ninguno automático |

Los límites son elecciones iniciales de este paquete, no valores óptimos universales. Cambiarlos requiere revisar código y tests. `inspect --min-lines N` ajusta solo la señal orientativa de tamaño. No controla herramientas del anfitrión.

El bloqueo por nombres no es un detector completo de secretos ni un sistema DLP. **No interpreta `.gitignore`.** La selección explícita requiere revisión. El runner evita registrar cuerpos, pero el proveedor y un adaptador propio pueden tener registros. Los temporales del adaptador de comando pueden persistir en almacenamiento del sistema según su comportamiento. Los chequeos de rutas no sustituyen una sandbox frente a modificaciones concurrentes maliciosas del filesystem.

## Resultados

Lectura: JSON con hechos, evidencia comprobada por coincidencia literal, hashes y `line_candidates` calculados localmente. Varias apariciones producen varios rangos; no se inventa una ubicación única. `read_paths` es cobertura declarada por el modelo, no prueba de que comprendió cada línea. `semantic_verification` recuerda la revisión pendiente.

Generación: manifiesto de `.io-delegation/candidates/<destino>`, tamaño, hash y `validation:"not_run"`. Una salida correcta de transporte no certifica sintaxis ni comportamiento. El runner no ejecuta tests ni integra el candidato automáticamente.

`--dry-run` comprueba el corpus y muestra su manifiesto, pero no comprueba credenciales, disponibilidad ni calidad del modelo. No muestra el contenido de los archivos y no realiza una llamada de red.
