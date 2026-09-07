# I/O Delegation

### Menos contexto innecesario. Más atención a lo que importa

[![Pruebas offline](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)

[English](README.md) · [Skill](skills/io-delegation/SKILL.md) · [Instalación](docs/INSTALLATION.md) · [Adaptadores](skills/io-delegation/references/ADAPTERS.md)

Una skill portable para separar exploración, trabajo repetitivo y decisiones de ingeniería. Funciona como instrucciones para **Claude Code, Codex, Cursor y otros agentes que lean Markdown**. Incluye un ejecutor opcional para auxiliares locales o remotos aprobados.

El principal conserva arquitectura, depuración, decisiones sensibles, edición exacta y verificación. Una búsqueda o una lectura corta tienen prioridad sobre delegar por rutina.

```text
Localizar → clasificar → herramienta, lectura dirigida o auxiliar
          → verificar evidencia → razonar, editar e integrar
```

## Instalar

```bash
git clone https://github.com/pnll1991/io-delegation-skill.git
cd io-delegation-skill
```

Elegí el comando de tu agente y reemplazá la ruta por un proyecto existente:

```bash
python install.py --agent claude-code --project "RUTA_DEL_PROYECTO"
python install.py --agent codex --project "RUTA_DEL_PROYECTO"
python install.py --agent cursor --project "RUTA_DEL_PROYECTO"
```

No ejecutes los tres por rutina: Codex y Cursor comparten `.agents/skills/io-delegation/`. Claude Code usa `.claude/skills/io-delegation/`. El instalador requiere Python 3.10+, copia la carpeta completa y no sobrescribe instalaciones existentes. No modifica reglas, permisos, modelos ni configuraciones del agente.

Para instalar a nivel usuario, reemplazá `--project "..."` por `--global`. Para simular, añadí `--dry-run`. En Windows podés usar `py -3`; en otros entornos, `python3`.

Sin Python, copiá manualmente la carpeta completa `skills/io-delegation/` al destino correspondiente. Las instrucciones no necesitan Python ni un segundo modelo. Las instalaciones globales son locales a esa máquina; un agente remoto necesita su propia copia accesible.

## Usar

> Aplicá la skill io-delegation a esta tarea. Localizá primero los archivos relevantes. Priorizá herramientas deterministas y lecturas dirigidas. Delegá solo a un auxiliar disponible, aprobado y con contexto separado. Verificá evidencia antes de decidir. Sin auxiliar, continuá con lectura selectiva.

Para un agente sin descubrimiento de skills, indicá la ruta real a `SKILL.md` y pedile que lo lea. Consultá [activación y convivencia](docs/INSTALLATION.md).

## Probar sin configurar un modelo

Estos ejemplos funcionan con archivos del propio repositorio y no llaman a servicios:

```bash
python skills/io-delegation/scripts/io_delegate.py inspect --root . --paths install.py
python skills/io-delegation/scripts/io_delegate.py bulk-read --root . --paths install.py --question "¿En qué carpetas puede escribir el instalador?" --dry-run
python -m unittest discover -s tests -v
```

`bulk-read` devuelve hechos acotados con evidencia literal y cobertura declarada. `code-write` guarda un archivo nuevo en `.io-delegation/candidates/`; nunca lo aplica ni ejecuta automáticamente. El transporte real se configura siguiendo [ADAPTERS.md](skills/io-delegation/references/ADAPTERS.md). No se activa ningún proveedor por defecto.

## Qué incluye

La carpeta instalable contiene la skill, el script, ejemplos y referencias de flujo, adaptadores, validación y fuentes. El repositorio agrega instalador, 75 pruebas offline, CI, documentación, licencia MIT y guías de contribución.

La ejecución Linux previa a publicar aprobó 75 pruebas. El estado real de la matriz de GitHub está en el badge; los tests de infraestructura no demuestran selección automática en agentes ni ahorro con modelos. Ver [alcance de pruebas](docs/TESTING.md).

Inspirada en Spotify Engineering y `shunt`, con contratos y controles propios. [Procedencia y diferencias](skills/io-delegation/references/SOURCES.md). No hay afiliación con Spotify ni se redistribuye su artículo.

No promete un porcentaje fijo de ahorro: hay que medir principal, auxiliar, latencia y retrabajo. Leer [VALIDATION.md](skills/io-delegation/references/VALIDATION.md) y [SECURITY.md](SECURITY.md).

[MIT](LICENSE) · Versión 0.1.0 · [Contribuir](CONTRIBUTING.md)
