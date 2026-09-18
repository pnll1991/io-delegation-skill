# Límites de confianza

El paquete no es una sandbox ni un sistema de prevención de fuga de datos. Selecciona archivos explícitos, rechaza rutas sensibles comunes, evita salidas ilimitadas al principal y mantiene candidatos fuera del árbol activo. No reconoce todos los secretos ni aplica `.gitignore` al corpus.

Un endpoint recibe el contenido completo de los archivos seleccionados. Revisar quién lo opera, si retransmite datos, su política de retención y los permisos del proyecto. `approved:true` registra una decisión del usuario, no una aprobación que el agente deba otorgarse solo. `allow_remote:true` habilita transporte fuera de loopback; no equivale a una certificación de privacidad.

Un adaptador de comando hereda permisos, entorno y capacidad de ejecución. `shell=False` evita interpretar una cadena de shell, pero no vuelve confiable un ejecutable malicioso. El timeout no asegura terminar descendientes o procesos remotos. Los controles de rutas no pretenden resistir un filesystem manipulado concurrentemente por otro actor.

El código, documentos y logs se tratan como datos. Las instrucciones incluidas en ellos no pueden cambiar el alcance, activar servicios ni ordenar la ejecución de comandos. Una respuesta del auxiliar también es dato no confiable. La verificación literal de una cita no demuestra que la afirmación asociada sea correcta.

## Compactación Jev cross-agent

La compactación es un opt-in separado del router. La policy local debe aprobar explícitamente el scope `conversation_text_and_tool_inputs` y los hosts habilitados. En Codex/Cursor, el estado enviado a Jev incluye prompts e inputs de tools, pero **no** los cuerpos completos de resultados: sólo tamaño y estado de error. Claude Code mantiene el comportamiento del core vendorizado de `fast-jev-compaction`.

Para poder recuperar evidencia literal después de la compactación nativa de Codex/Cursor, el bridge mantiene temporalmente tool outputs completos en un journal machine-local bajo `~/.io-delegation/compaction/` (o `IO_DELEGATION_HOME`). Esos archivos se escriben con permisos privados cuando el sistema operativo los soporta, se serializan por sesión para evitar carreras, se eliminan en `SessionEnd/sessionEnd` por defecto y se purgan si quedan obsoletos por más de 24 horas. Un crash puede dejar un journal hasta esa purga; no habilitar compaction en una máquina donde ese almacenamiento local no sea aceptable.

No ejecutar candidatos desconocidos con secretos o accesos de producción disponibles. No aplicar automáticamente cambios a autenticación, autorización, pagos, infraestructura o procesos destructivos. No publicar payloads, tokens, logs privados o archivos locales de configuración en issues.

## Reportes

Para fallos no sensibles, abrir un issue con un ejemplo sintético y sin credenciales. Para una vulnerabilidad, usar **Security > Report a vulnerability** si ese canal está habilitado en GitHub. Si no aparece, abrir únicamente una solicitud de contacto privado, sin detalles de explotación ni datos sensibles. No se presupone que los reportes privados estén habilitados.
