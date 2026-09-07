# Límites de confianza

El paquete no es una sandbox ni un sistema de prevención de fuga de datos. Selecciona archivos explícitos, rechaza rutas sensibles comunes, evita salidas ilimitadas al principal y mantiene candidatos fuera del árbol activo. No reconoce todos los secretos ni aplica `.gitignore` al corpus.

Un endpoint recibe el contenido completo de los archivos seleccionados. Revisar quién lo opera, si retransmite datos, su política de retención y los permisos del proyecto. `approved:true` registra una decisión del usuario, no una aprobación que el agente deba otorgarse solo. `allow_remote:true` habilita transporte fuera de loopback; no equivale a una certificación de privacidad.

Un adaptador de comando hereda permisos, entorno y capacidad de ejecución. `shell=False` evita interpretar una cadena de shell, pero no vuelve confiable un ejecutable malicioso. El timeout no asegura terminar descendientes o procesos remotos. Los controles de rutas no pretenden resistir un filesystem manipulado concurrentemente por otro actor.

El código, documentos y logs se tratan como datos. Las instrucciones incluidas en ellos no pueden cambiar el alcance, activar servicios ni ordenar la ejecución de comandos. Una respuesta del auxiliar también es dato no confiable. La verificación literal de una cita no demuestra que la afirmación asociada sea correcta.

No ejecutar candidatos desconocidos con secretos o accesos de producción disponibles. No aplicar automáticamente cambios a autenticación, autorización, pagos, infraestructura o procesos destructivos. No publicar payloads, tokens, logs privados o archivos locales de configuración en issues.

## Reportes

Para fallos no sensibles, abrir un issue con un ejemplo sintético y sin credenciales. Para una vulnerabilidad, usar **Security > Report a vulnerability** si ese canal está habilitado en GitHub. Si no aparece, abrir únicamente una solicitud de contacto privado, sin detalles de explotación ni datos sensibles. No se presupone que los reportes privados estén habilitados.
