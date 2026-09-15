---
name: io-delegation
description: Delega consultas factuales sobre código extenso o generación repetitiva a un worker aprobado con contexto separado. Prioriza herramientas deterministas y lecturas dirigidas; no actives un modelo adicional por rutina. Conserva depuración, arquitectura, seguridad y edición exacta en el principal.
license: MIT
metadata:
  version: "0.3.0"
---

# I/O Delegation

Optimizá el trabajo terminado correctamente, no el número de llamadas. Para una búsqueda o extracción que resuelve un parser local, no invoques un worker.

## Elegí la ruta

Usá búsqueda determinista o lectura por rango primero. No imprimas una línea minificada entera: preferí `rg --files`, `rg -l` o `scripts/bounded_search.py --root . --paths archivo --text símbolo`. El último limita la salida y declara coincidencias omitidas.

Para hechos dispersos en archivos extensos, usá `bulk-read` cuando exista un adaptador explícitamente aprobado y el resultado acotado pueda reducir el trabajo total. No cargues primero el corpus en el principal para luego delegarlo. El runner lee los archivos elegidos. No envíes repositorios completos, secretos ni datos personales.

La depuración, causalidad, arquitectura, concurrencia, pagos, seguridad y modificación de código existente quedan en el principal, con evidencia original. Una cita literal verifica localización, no corrección semántica.

## Conectá el worker explícitamente

Instalar esta skill o sus hooks NO configura ni inicia un worker. Se necesita un JSON aprobado y una prueba directa exitosa. No crees proveedores, apruebes configuraciones ni copies credenciales automáticamente. Ver [WORKERS.md](references/WORKERS.md) solo para configurar o diagnosticar.

En el benchmark actualizado, la configuración está en `.io-delegation/worker.local.json`. Usá el ejecutable Python verificado que indique el entorno; no ensayes lanzadores repetidamente.

```text
python RUTA_SKILL/scripts/io_delegate.py bulk-read --root . --config .io-delegation/worker.local.json --paths src/a.py src/b.py --question "¿Qué funciones escriben registros y con qué llamadas?"
```

`RUTA_SKILL` significa la ruta real de la carpeta de esta skill. El runner admite `codex-cli`, `command` y `chat-completions`; no cambia entre ellos al fallar. `codex-cli` abre un hilo independiente, efímero, sin historial del principal, con sandbox de solo lectura. Usar el mismo modelo NO garantiza menor costo.

Verificá `status`, cobertura, evidencia literal y hash. Ante `insufficient_context`, evidencia inválida, timeout o fuentes modificadas, no inventes una respuesta: ampliá una consulta acotada solo si compensa o continuá con lectura dirigida. No recurses ni evadas el presupuesto.

## Generación y cierre

`code-write` exige referencia, especificación cerrada y destino nuevo; guarda un candidato en `.io-delegation/candidates/`, nunca lo aplica ni ejecuta. Validá y revisá antes de promoverlo. No cambies permisos, instales dependencias, hagas commits ni publiques por tu cuenta.

Los intentos, despachos, respuestas y errores se registran en `.io-delegation/worker-events.jsonl` sin código ni secretos. El consumo desconocido no es cero. No declares ahorro sin comparar calidad, tokens principales y del worker, tiempo y precios reales.

Los hooks solo controlan herramientas cubiertas: no disparan modelos automáticamente ni garantizan ahorro. No los eludas ni desactives. Consultá [ENFORCEMENT.md](references/ENFORCEMENT.md) para cobertura y confianza del cliente. Un hook instalado no equivale a uno ejecutado.
