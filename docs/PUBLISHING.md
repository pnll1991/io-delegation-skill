# Mantenimiento y distribución

Repositorio: [pnll1991/io-delegation-skill](https://github.com/pnll1991/io-delegation-skill).

La unidad instalable es `skills/io-delegation/`, con su licencia, referencias, assets y script. Mantenerla autocontenida. El resto del repositorio distribuye documentación, instalador y pruebas, no es necesario copiarlo al proyecto receptor.

## Checklist de una nueva versión

1. Revisar el diff y ejecutar `python -m unittest discover -s tests -v`.
2. Comprobar que no haya credenciales, configuraciones aprobadas, datos de clientes, candidatos generados ni logs privados.
3. Actualizar la versión en `SKILL.md`, el changelog y el README cuando corresponda.
4. Publicar los cambios y consultar los resultados de la matriz de CI para ese commit.
5. Para una release, crear un tag explícito y adjuntar la carpeta autocontenida; no anunciar una release hasta que exista.

Un commit en `main`, una release etiquetada y un benchmark son artefactos distintos. El repositorio se instala directamente desde un clon o un ZIP de GitHub; no exige marketplace ni release adjunta.

No añadir porcentajes de ahorro o compatibilidad ensayada sin evidencia. Mantener [TESTING.md](TESTING.md), [CONTRIBUTING.md](../CONTRIBUTING.md) y [SECURITY.md](../SECURITY.md) actualizados.
