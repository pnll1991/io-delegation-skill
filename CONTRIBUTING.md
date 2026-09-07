# Contribuir

Mantener el núcleo independiente del anfitrión. Una mejora no debe exigir un plugin de Claude Code, un SDK de OpenAI, Cursor Rules, Bash, un proveedor de modelos ni una plataforma SaaS.

Antes de proponer cambios, ejecutar `python -m unittest discover -s tests -v`. Incluir el caso que motivó el cambio y un test que falle sin la corrección. Separar pruebas offline de ensayos con modelos reales. No incluir secretos, código de clientes o payloads privados.

Al modificar el flujo, actualizar `SKILL.md` sin convertirlo en un manual extenso; mover detalles a referencias cargadas bajo demanda. Los nuevos adaptadores deben conservar el contrato de entrada/salida, el comportamiento ante truncamiento, la autorización explícita y los límites de datos.

No aceptar benchmarks sin metodología, comparación equivalente y verificación funcional. Reducir bytes en stdout no prueba reducción de tokens facturados. Documentar también fallos, latencia y retrabajo.

No cambiar porcentajes, badges o compatibilidad a «verificado» por agregar un workflow: registrar las ejecuciones reales correspondientes.
