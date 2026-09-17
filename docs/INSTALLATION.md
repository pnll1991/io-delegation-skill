# Instalación y convivencia

> **V1 recomendada:** para la instalación como Context Gateway (skill + MCP + routing + doctor), usar [INSTALLATION_V1.md](INSTALLATION_V1.md). Este documento conserva el flujo manual y detalles de convivencia.

## Carpeta autocontenida

El artefacto instalable es `skills/io-delegation/`. Contiene su propia licencia, recursos y script. No depende de rutas fuera de esa carpeta. `install.py` copia ese directorio; no modifica `AGENTS.md`, `CLAUDE.md`, reglas, ajustes, modelos ni permisos.

Si existe una instalación previa, se detiene. Para actualizar, revisar diferencias y conservar una copia de la versión anterior antes de reemplazarla manualmente. Para desinstalar, eliminar únicamente la carpeta de esta skill después de verificar su ruta; las configuraciones de proveedores están separadas y no se eliminan.

## Descubrimiento por agente

**Claude Code.** La documentación indica `.claude/skills/<nombre>/SKILL.md` en el proyecto o `~/.claude/skills/<nombre>/SKILL.md` para uso personal. Se puede mencionar la skill o usar `/io-delegation` [C1].

**Codex.** Usa `.agents/skills/<nombre>/SKILL.md` en el proyecto y `~/.agents/skills/<nombre>/SKILL.md` a nivel usuario. Se puede mencionar con `$io-delegation` o seleccionarla mediante el mecanismo de skills disponible [C2].

**Cursor.** Reconoce `.agents/skills` y `.cursor/skills`, además de ubicaciones de compatibilidad. Este instalador elige `.agents/skills` para compartir una sola copia con Codex. También permite invocación desde el menú `/` [C3].

[C1]: https://code.claude.com/docs/en/skills
[C2]: https://developers.openai.com/codex/skills
[C3]: https://cursor.com/docs/skills

Estas activaciones son comodidades del anfitrión, no partes del núcleo. Una organización puede restringir la carga de skills. Si no aparece, revisar la ruta y sus políticas; probar una sesión nueva. No cambiar permisos o políticas administradas para forzarla.

Las instalaciones globales son locales a la máquina. En agentes cloud, sesiones remotas o contenedores, usar una copia accesible en ese entorno; no asumir sincronización automática desde `~/.agents/skills`.

## Usar varios agentes en el mismo proyecto

Codex y Cursor pueden compartir `.agents/skills/io-delegation/`. Claude Code usa su ubicación documentada `.claude/skills/io-delegation/`. Cursor también descubre directorios de compatibilidad: al coexistir varias copias, verificar el selector y evitar versiones divergentes. No asumir que todos los agentes deduplican igual.

Mantener `skills/io-delegation/` como fuente única del repositorio distribuidor y actualizar instalaciones conscientemente. Un symlink puede evitar copias en sistemas que lo admitan, pero no se crea automáticamente ni se exige en Windows. El instalador rechaza destinos que salgan del ámbito mediante enlaces.

## Agentes sin soporte de skills

Dar el archivo explícitamente:

> Leé `RUTA_A_LA_SKILL/SKILL.md`. Usá sus decisiones de enrutamiento para esta tarea. No cargues las referencias salvo que hagan falta. No presupongas terminal ni auxiliar si este entorno no los ofrece.

Eso permite adoptar el procedimiento, no agrega auto-descubrimiento o enforcement. Si el agente admite un archivo de instrucciones de proyecto, se puede añadir manualmente una referencia breve a la skill, sin copiar todas sus reglas en varios lugares.

## Configuración y candidatos

Las configuraciones reales no van dentro del paquete público. Usar un archivo externo revisado, como `worker.local.json`, que el runner recibe por `--config`. Añadir al `.gitignore` del proyecto receptor:

```gitignore
worker.local.json
.io-delegation/
```

El instalador no modifica el `.gitignore` de otros proyectos. El usuario o el agente, con autorización para ese proyecto, incorpora las exclusiones.
