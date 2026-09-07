# Playbook operativo

Este documento especifica decisiones de esta implementación. No presenta sus heurísticas como resultados experimentales de Spotify.

## Preflight en una línea

Antes de una delegación registrá: `ruta | pregunta | archivos | destino del auxiliar | criterio de aceptación`. No hace falta anunciar cada operación al usuario: lo importante es tener un alcance verificable y respetar permisos.

Ejemplo: `bulk-read | localizar escrituras | service.py, repository.py | endpoint local aprobado | símbolos + evidencia literal + cobertura completa`.

El valor de una delegación depende de la diferencia entre el volumen de entrada del auxiliar y la información útil que necesita el principal, además de las tarifas, la calidad y la latencia. Una consulta sobre una función identificada suele resolverse con su definición y llamadas cercanas. Leer seis archivos completos para encontrarla no es el punto de partida.

## Secuencia preferida

1. Localizar con árbol de archivos, búsqueda de símbolos o herramienta del lenguaje. Pedir nombres de archivos y coincidencias acotadas, no listados ilimitados.
2. Inspeccionar tamaño, tipo y permiso del conjunto candidato. El inspector lee bytes localmente, pero solo devuelve metadatos al agente.
3. Clasificar qué es extracción mecánica y qué requiere juicio. Separar ambos trabajos.
4. Ejecutar una operación determinista, una lectura dirigida o una delegación de alcance cerrado.
5. Validar evidencia, cobertura, vigencia y utilidad. Volver al original donde la decisión lo necesite.
6. Resolver la pregunta, editar o integrar con el principal. Medir el trabajo completo, no solo la llamada favorable.

Ejemplos de herramientas, sin dependencia obligatoria: búsqueda del editor, `rg -n`, `git grep -n`, `Select-String`, parser AST, compilador y suite de tests. Si `rg` no está instalado, no lo instales por rutina: usá lo que haya. Una tubería no vuelve una lectura barata por definición; importa cuánto texto llega al principal.

## Casos trabajados

### A. Entender un flujo sin abrir todo

Pedido: «¿Qué módulos consumen `InvoiceCreated`?».

Buscá primero el símbolo. Si hay tres coincidencias inequívocas, leelas directamente. Si aparecen muchas variantes en varios servicios, elegí archivos candidatos y preguntá al lector por sus suscripciones, con evidencia y lista de archivos revisados. Contrastá las coincidencias relevantes antes de explicar el flujo. No uses el resumen para concluir que la entrega es exactamente una vez, que no hay carreras o que no existen otros consumidores fuera del corpus.

### B. Generar tests repetitivos

Pedido: «Agregá tests para el parser siguiendo los existentes».

El principal determina qué contrato comprobar y qué casos faltan. Selecciona un test de referencia y las definiciones del parser. El auxiliar produce un nuevo archivo de tests, sin instalar paquetes. El candidato permanece aislado hasta verificar imports, assertions significativas, casos borde y ejecución controlada. No se compara la salida del parser contra otra llamada al mismo parser como supuesto oráculo. Si la función depende de un tipo no provisto, se añade ese contexto; no se inventa.

### C. Investigar cobros duplicados

No delegar el diagnóstico. El principal inspecciona idempotencia, transacciones y las rutas reales de ejecución. Puede pedir un inventario factual de lugares donde aparece una clave o se llama a un repositorio, con datos redactados; no pedir «decidí si es seguro» a un auxiliar económico.

### D. Ajustar una línea conocida

No resumir un archivo de 2.000 líneas para editar un import. Localizar el import, leer la sección y hacer un parche exacto. La escala del archivo no manda sobre la naturaleza de la operación.

### E. Sin auxiliar, sin terminal o con material ya en contexto

Sin auxiliar: extracción determinista y lectura dirigida. Sin terminal: las mismas decisiones con búsqueda y lectura del anfitrión. Si todo el material ya está en el contexto principal, no fingir ahorro retroactivo ni enviarlo a otro modelo para resumirlo. La política sigue siendo útil; el beneficio de desviar tokens no existe en esa operación.

### F. Generación extensa o resultado incompleto

Dividir por archivos y contratos independientes. No cortar arbitrariamente una función ni aceptar código truncado. Límite recomendado de este paquete: una unidad nueva y revisable por llamada. El principal conserva las decisiones de interfaces compartidas. No encadenar auxiliares hasta perder trazabilidad.

## Contrato para un auxiliar nativo

Puede usarse un subagente del entorno sin el script solo si tiene contexto realmente separado y respeta permisos y destino aprobado. Entregar rutas o handles que pueda leer, pregunta cerrada y formato compacto. Confirmar que no hereda automáticamente todo el historial ni ejecuta herramientas innecesarias. No llamar «más barato» a un subagente cuyo modelo o tarifa no se conoce.

El lector debe devolver alcance revisado, hechos, símbolos, evidencia y desconocidos. El generador debe devolver un candidato, manifiesto y estado de validación. Sin el script, el principal debe verificar manualmente los controles equivalentes: no afirmar que el host los aplica automáticamente.

## Extender sin convertirlo en un framework

Un modo para documentación puede transformar hechos ya verificados en una página siguiendo una plantilla; uno para traducción puede preservar claves y placeholders. Ambos necesitan contratos propios y tests. No reutilizar `code-write` ciegamente para revisión de seguridad, migraciones o decisiones de arquitectura. Cambiar el proveedor debe modificar el adaptador, no las reglas del flujo.
