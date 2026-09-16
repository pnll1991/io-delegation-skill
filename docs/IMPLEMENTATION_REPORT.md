# Implementación de la auditoría · 16 de septiembre de 2026

Base: `04511d2`. Seguimiento: issue #2. Rama: `codex-io-delegation-ab`.
Las diez tareas se implementaron y probaron por etapas en commits locales; se
publican juntas para no exponer una rama parcialmente integrada. El paquete de
entrega conserva el historial por etapa, logs y hashes de los archivos.

## Cambios aplicados

T01: contrato breve de uso MCP; instalación y compatibilidad separadas. No pide
buscar una skill global supuesta ni iniciar Python dentro del shell restringido.

T02: servidor `io_context` con búsqueda literal/globs autorizados y extracción
estática HTML, JSON Pointer, líneas y spans. Ninguna de esas operaciones llama a
un modelo. Fuentes compartidas, campos ausentes y resultados parciales explícitos.

T03: selección de fragmentos antes de inferencia; AST Python con imports,
decoradores y definiciones; rangos/literales para otros lenguajes. La evidencia se
valida contra los fragmentos enviados, no contra texto que el worker no vio.
No hay análisis AST JavaScript ni garantía de cobertura de dependencias externas.

T04: varias proyecciones en una operación y hasta cuatro preguntas relacionadas
sobre el mismo corpus; una tabla de fuentes y referencias compactas.

T05: semántica opcional con la configuración ya aprobada. La vía HTTP mínima usa
mensajes seleccionados sin arrancar Codex. JSON Schema es opt-in según capacidad
del backend. Microcomparador preparado; no se conectó un nuevo proveedor.

T06: caché exacta ligada a repositorio, permisos, fuentes, consulta, configuración
y código. Invalida cambios. Es distinta de la caché del proveedor. Puede guardar
fragmentos: el directorio de auditoría queda privado, fuera del proyecto.

T07: límites de bytes, salida y llamadas; freno entre llamadas por tokens
reportados. La CLI no tiene un límite duro de tokens verificado aquí: la última
respuesta puede cruzar el presupuesto. Uso desconocido bloquea nueva inferencia.
Respuestas rechazadas siguen en el consumo; no se trunca JSON para hacerlo pasar.

T08: fuente estable antes de pregunta variable; micropruebas frío/repetición
exacta/pregunta distinta. No se garantiza cache hit del proveedor.

T09: telemetría de operaciones/worker y análisis del principal. El número de
inferencias internas de Codex queda desconocido si el stream no lo expone.
Comandos, turnos y llamadas MCP no se presentan como inferencias equivalentes.

T10: preparación A/B/C, migración del manifiesto anterior sin reemplazarlo,
verificación MCP y validadores sintéticos independientes. A es el control actual;
B agrega operaciones locales; C agrega semántica opcional. El costo por tarea
correcta incluye fallos conocidos, y la contabilidad incompleta detiene la tanda.

## Validación ejecutada

- 104 tests de la nueva ruta aprobados localmente en Linux. Incluyen invariantes
  heredadas en pruebas de presupuesto, parsers, límites, caché, evidencia y uso.
- 30 tests de la ruta MCP anterior aprobados. Los módulos `worker_mcp.py`,
  `worker_runtime.py` e `io_delegate.py` permanecen sin cambios.
- Ensayo A/B/C simulado de nueve ejecuciones usando Git worktrees, servidor MCP y
  validadores reales. Los valores de uso del simulador son ficticios de prueba,
  no una medición de ahorro ni de modelos.
- HTTP local de prueba verifica el transporte mínimo sin lanzar un agente.
  La respuesta del servidor es sintética, no una nueva inferencia.

Reproducción: ver `benchmarks/context/README.md`. El preflight por defecto no
llama a modelos. `--live` autoriza inferencias; no se ejecutó en esta entrega.

## Pendiente, no marcado como éxito

La PC autorizada estaba offline. No se instaló v0.4 en ese Windows ni se ejecutó
una nueva verificación autenticada, microcomparación de precios/backends o A/B/C
con modelos reales. El umbral de adopción del 20% es una meta predeclarada, no un
resultado. La web, configuración global y resultados históricos no se tocaron.
No se copiaron credenciales ni se cambió de proveedor.
