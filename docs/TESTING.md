# Alcance de la validación

## Reproducir

```text
python -m unittest discover -s tests -v
```

La suite contiene **84 pruebas offline**: 75 del ejecutor, transporte, instalación y paquete; 9 del benchmark. La ejecución previa a publicación del 7 de septiembre de 2026 aprobó 75 pruebas. La ampliación inicial a 83 pasó las seis combinaciones de GitHub Actions; la novena prueba del benchmark cubre valores Python con separadores numéricos, como `6_000`. El tiempo de esta suite no es un benchmark de modelos.

Se verifican selección y límites de archivos, UTF-8, hashes, exclusiones por rutas y enlaces, formato de resúmenes, evidencia literal, cobertura declarada, fuentes modificadas, candidatos sin sobrescritura, configuración aprobada, transporte de comando, timeouts, errores sin revelar cuerpos privados, HTTP en loopback, truncamiento, respuestas malformadas, redirecciones bloqueadas, formato de la skill, enlaces relativos e instalación autocontenida.

Las pruebas del benchmark verifican selectores sobre el código real, respuestas esperadas, inclusión de la skill completa, control ya focalizado, formatos estrictos, suma del consumo de llamadas fallidas y conservación de resultados negativos. La lectura de evidencia numérica usa AST y no ejecuta código.

Los tests de `tests/` usan programas stub y un servidor HTTP sintético. **No consultan modelos ni proveedores externos.** La prueba de delimitación de contenido verifica el JSON, no demuestra resistencia de un modelo real a prompt injection.

## GitHub Actions

[Ver ejecuciones y logs](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml).

La matriz incluye Linux, Windows y macOS con Python 3.10 y 3.13: seis combinaciones. El badge muestra el resultado del workflow, no una afirmación estática de compatibilidad. Revisar el commit utilizado. Las pruebas de symlinks pueden omitirse en sistemas que no permitan crearlos; conservar las omisiones al reportar resultados.

Los workflows solicitan únicamente lectura del contenido y no persisten credenciales de checkout. El conteo de tokens tiene un workflow liviano separado. El piloto con un modelo descargado es opcional y se activa manualmente.

## Benchmark con inferencia, separado de los tests

El 7 de septiembre de 2026 se completó un piloto controlado con Qwen2.5-Coder-1.5B-Instruct, además de un conteo BPE con dos tokenizadores. [Metodología y resultados](../benchmarks/README.md).

Se midieron tokens reales de entrada y salida del modelo y tiempos de cada llamada, no facturación ni tiempo de extremo a extremo. **Solo 1 de 9 respuestas principales pasó el formato JSON estricto.** Los resultados originales conservan los fallos; una comprobación posterior de valores sin bloques Markdown no sustituye ese criterio. La delegación forzada no produjo hallazgos y gastó más tokens en total al incluir la alternativa de lectura dirigida.

Que el workflow termine correctamente significa que ejecutó y guardó el experimento, **no** que todas las respuestas aprobaron calidad ni que la skill demostró ahorro de una tarea completada correctamente.

## Qué sigue sin demostrarse

Las ubicaciones de instalación se contrastaron con documentación oficial, pero no se abrieron sesiones reales de Claude Code, Codex o Cursor ni se midió su selección automática de skills. Tampoco se midieron ahorro monetario, calidad de cambios de código o generalización a proyectos de producción.

No se ejecutó el validador externo `skills-ref`: los tests comprueban los campos utilizados, el nombre, el tamaño y las referencias, sin añadir una dependencia de distribución.

El procedimiento para evaluaciones completas está en [VALIDATION.md](../skills/io-delegation/references/VALIDATION.md).
