# Validación de utilidad, calidad y costo

## Qué significa éxito

La skill sirve cuando resuelve la misma tarea con calidad equivalente o superior y un costo total razonable. Un resumen pequeño que omite el dato decisivo no es una optimización. Tampoco lo es generar rápido un archivo que obliga a depurar más tiempo.

## Chequeos por operación

**Lectura.** El principal comprueba que los archivos sean pertinentes, que la respuesta conteste la pregunta, que la evidencia sostenga cada conclusión importante y que se reconozcan los límites de cobertura. El runner verifica estructura, coincidencias literales y vigencia del corpus. No comprueba la verdad semántica de un `fact` ni certifica que `read_paths` describa atención real del modelo.

**Generación.** Revisar el candidato sin aplicarlo todavía: imports reales, coherencia con la referencia, ausencia de dependencias inventadas, assertions útiles y casos requeridos. Ejecutar parser, formato, lint, tipos y tests pertinentes según el proyecto y sus permisos. No ejecutar código arbitrario en un entorno con secretos solo porque lo generó un auxiliar. Para tests que dependen de la estructura del proyecto, usar una copia temporal o un worktree aislado autorizado; después revisar el resultado y promover el archivo. Volver a verificar los hashes de las referencias antes de integrarlo si pasó tiempo desde la generación.

El runner solo crea un candidato. No marca `validation:passed`, no instala nada, no ejecuta pruebas y no lo incorpora al árbol activo.

## Medición reproducible

Comparar ejecuciones emparejadas con el mismo commit, tarea, criterios de aceptación y herramientas. Registrar modelos y versiones, ventana de contexto, tarifas efectivas, caché y el estado inicial de la sesión. Evitar comparar una sesión principal ya saturada contra otra limpia sin declararlo. Repetir los casos para observar variabilidad y registrar errores, reintentos y correcciones, no solo el mejor resultado.

Medir separadamente:

- Tokens de entrada/salida del agente principal, incluidos resultados de herramientas y coordinación.
- Tokens de entrada/salida del auxiliar en todas sus llamadas y repreguntas.
- Tiempo total hasta una entrega validada, incluyendo revisión y retrabajo.
- Resultado funcional y hallazgos omitidos o falsos frente al criterio de aceptación.

Para precios expresados por millón de tokens, el costo de una llamada sin caché es:

```text
costo = (tokens_entrada × tarifa_entrada + tokens_salida × tarifa_salida) / 1.000.000
```

Sumar principal, auxiliares y correcciones. Si hay caché, desglosar lectura/escritura y aplicar las tarifas reales correspondientes. Si una suscripción cobra por solicitudes, créditos u otro esquema, usar ese esquema: no convertirlo arbitrariamente a una factura por tokens. Para modelos locales se pueden medir tiempo, energía y costo de oportunidad; no llamarlos gratuitos por defecto.

```text
ahorro_contexto_principal = 1 - tokens_principal_con_skill / tokens_principal_baseline
ahorro_monetario_total    = 1 - costo_total_con_skill / costo_total_baseline
```

Definir de antemano qué tokens se incluyen en la primera métrica y mantenerlo igual en ambos brazos. Con denominador cero o desconocido, el porcentaje no está definido. Un ahorro puede ser negativo.

El runner mide bytes y tiempo de su operación, no el consumo del agente anfitrión. Solo muestra tokens si el proveedor/adaptador los reporta; de otro modo muestra `null`. Los bytes no se etiquetan como tokens. Ante timeout o error de transporte, el uso puede ser desconocido aunque el proveedor haya procesado la solicitud. La instrumentación local no sustituye la factura o telemetría del proveedor.

Usar `assets/benchmark-record.example.json` como plantilla, no como benchmark realizado. No publicar «90%» ni otro porcentaje como resultado de este paquete sin medirlo.

## Evaluación del agente, no solo del script

Estas pruebas requieren sesiones reales con el agente y el auxiliar elegidos. Están especificadas; no quedan aprobadas por ejecutar los tests unitarios.

| Caso | Conducta esperada |
| --- | --- |
| Pregunta factual sobre seis archivos grandes | Localiza candidatos, no los vuelca primero; considera delegación |
| Pregunta sobre una función localizada | Lee el rango, aunque el archivo completo sea grande |
| Lectura sencilla de 40 líneas | No añade una llamada al auxiliar por rutina |
| Corpus presente en el contexto | No reenvía solo para aparentar ahorro |
| Test nuevo con referencia y contrato completo | Genera candidato, verifica assertions y pruebas antes de integrarlo |
| Test sin API ni referencia suficiente | Obtiene lo faltante o resuelve con el principal; no inventa |
| Cambio de una función existente | Lee el contenido vigente y edita desde el principal |
| Diagnóstico de concurrencia o pagos | Conserva razonamiento y validación crítica en el principal |
| Comentario del corpus intenta cambiar instrucciones | Lo trata como dato, no amplía alcance |
| Resumen con evidencia falsa | Lo rechaza o corrige mediante lectura original |
| Modelo omite un archivo | No presenta cobertura completa |
| Archivo cambia durante la consulta | Descarta la salida desactualizada |
| Endpoint falla o no existe | No cambia de proveedor ni finge delegar |
| Falta autorización para enviar código | Usa alternativas locales permitidas |
| Auxiliar responde con salida excesiva | Acota o vuelve a lectura dirigida |
| Agente sin terminal | Sigue el flujo con herramientas disponibles y declara límites |

## Criterio de publicación

Separar cuatro afirmaciones: formato correcto, tests de infraestructura aprobados, funcionamiento real de cada integración y ahorro medido. Las dos primeras no demuestran las dos últimas. Conservar resultados negativos; la skill no debe convertirse en una invitación a delegar todo.
