# Alcance de la validación

## Reproducir

```text
python -m unittest discover -s tests -v
```

La ejecución previa a publicación del 7 de septiembre de 2026, en Linux con CPython 3.13.5, aprobó **75 pruebas sin fallos ni omisiones**. El tiempo de la suite no es un benchmark de modelos.

Se verifican selección y límites de archivos, UTF-8, hashes, exclusiones por rutas y enlaces, formato de resúmenes, evidencia literal, cobertura declarada, fuentes modificadas, candidatos sin sobrescritura, configuración aprobada, transporte de comando, timeouts, errores sin revelar cuerpos privados, transporte HTTP con un servidor sintético en loopback, truncamiento, respuestas malformadas, redirecciones bloqueadas, formato de la skill, enlaces relativos e instalación autocontenida.

Los tests usan programas stub y un servidor HTTP sintético. **No consultan modelos ni proveedores externos.** La prueba de delimitación de contenido verifica el JSON, no demuestra resistencia de un modelo real a prompt injection.

## GitHub Actions

[Ver ejecuciones y logs](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml).

La matriz incluye Linux, Windows y macOS con Python 3.10 y 3.13: seis combinaciones. El badge del README muestra el resultado del workflow, no una afirmación estática de compatibilidad. Revisar la ejecución correspondiente al commit que se usa. Las pruebas de symlinks pueden omitirse en sistemas que no permitan crearlos; las omisiones deben conservarse al reportar resultados.

Las acciones usan versiones mayores documentadas en [actions/checkout](https://github.com/actions/checkout) y [actions/setup-python](https://github.com/actions/setup-python). El workflow solicita únicamente lectura del contenido y no persiste credenciales de checkout.

## Qué no demuestran estas pruebas

Las ubicaciones de instalación se contrastaron con documentación oficial, pero la suite no abre sesiones reales de Claude Code, Codex o Cursor ni mide su selección automática de skills.

No se ejecutó el validador externo `skills-ref`: los tests incluidos comprueban los campos utilizados, el nombre, el tamaño y las referencias, sin añadir una dependencia de distribución.

No se midieron ahorro de tokens, costo monetario, calidad de un auxiliar real ni latencia de inferencia. El procedimiento y los casos de evaluación del agente están en [VALIDATION.md](../skills/io-delegation/references/VALIDATION.md).
