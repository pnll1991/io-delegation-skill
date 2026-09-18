# Instalación V1.2 — Context Gateway

## Camino recomendado

Desde el repositorio de I/O Delegation:

```powershell
# Windows
.\io-delegation.cmd setup --project D:\ruta\al\proyecto
```

```bash
# macOS / Linux
./io-delegation setup --project /ruta/al/proyecto
```

Antes de escribir nada se puede inspeccionar el plan:

```bash
io-delegation setup --project . --dry-run
```

`--dry-run` no crea marcador, runtime, skill, estado, backups ni configuración MCP.

## Qué instala

Setup crea dos capas:

1. En el proyecto: la skill y un marcador privado `.io-delegation/project.json` ignorado por Git.
2. En el usuario: runtime, estado, auditoría y un único MCP global `io_context` por host.

El MCP global resuelve el proyecto actual en tiempo de ejecución. Fuera de proyectos configurados expone cero tools en lugar de fallar.

## Registro por host

- **Codex:** `~/.codex/config.toml`. Un bloque administrado apunta al runtime global. Se validó en una sesión Codex real: el proceso MCP hereda el `-C` del proyecto.
- **Cursor:** `~/.cursor/mcp.json`. El servidor global usa `${workspaceFolder}` para pasar el proyecto actual y `${env:NAME}` para credenciales.
- **Claude Code:** user-scope MCP mediante `claude mcp add --scope user` cuando el CLI está instalado. Si no está instalado, `doctor` lo informa como pendiente sin crear un `.mcp.json` falso.

Las configuraciones de proyecto `.codex/config.toml`, `.cursor/mcp.json` y `.mcp.json` ya no reciben rutas absolutas generadas por setup.

El runtime estable vive en:

```text
~/.io-delegation/runtime/io-delegation/
```

Por eso mover o eliminar el clon distribuidor de I/O Delegation no rompe proyectos ya instalados.

## Proyectos movidos

Cada proyecto recibe un ID estable dentro de `.io-delegation/project.json`. Al mover la carpeta, el bootstrap encuentra ese marcador desde el workspace actual y sigue resolviendo el mismo estado.

`doctor` avisa si la ruta almacenada quedó antigua. Ejecutar setup otra vez refresca los metadatos sin cambiar el ID:

```bash
io-delegation setup --project /nueva/ruta
```

También actualiza automáticamente el ejecutable de Python registrado si cambió de ubicación.

## Smart routing

Jev está **off por defecto**, incluso si `TYPESAFE_API_KEY` ya está disponible. Esto evita activar routing externo por mera presencia de una credencial. Para probarlo deliberadamente:

```bash
io-delegation setup --project . --jev on
```

`--jev auto` sigue disponible como modo experimental/compatibilidad y puede crear una configuración aprobada fuera del proyecto cuando la variable existe. El MCP global hereda únicamente el nombre de la variable, nunca su valor.

```powershell
$env:TYPESAFE_API_KEY="..."
.\io-delegation.cmd setup --project .
```

Control explícito:

```bash
io-delegation setup --project . --jev on
io-delegation setup --project . --jev off
```

La key literal nunca se escribe en el estado, config de host, telemetría ni marcador del proyecto.

## Semantic worker y orquestación de compute

Para dejar un worker aprobado disponible, usar una configuración guardada fuera del proyecto:

```bash
io-delegation setup --project . --worker-config /ruta/privada/worker.json
```

Con un worker aprobado, setup usa `--orchestration auto` por defecto. Esto **no** significa enviar todo al worker: sólo candidatos factuales/bulk llegan al scorer Jev; thresholds locales de riesgo/suficiencia permiten como máximo un intento T1 barato, y evidencia inválida/incompleta escala al principal T2. Si falta `TYPESAFE_API_KEY`, el scorer falla cerrado a T2 y el worker no se despacha.

Control explícito y persistente:

```bash
io-delegation setup --project . --orchestration off
io-delegation setup --project . --orchestration on
```

El path histórico de auto-dispatch incondicional sigue separado y exige `"context_auto_dispatch": true`. Un `compute_profiles.cheap` opcional puede seleccionar un modelo/effort barato dentro del mismo adapter ya aprobado. Ver [ORCHESTRATION.md](../skills/io-delegation/references/ORCHESTRATION.md).

Para deshabilitar el worker existente:

```bash
io-delegation setup --project . --no-worker
```

## Read guard

El guard está **off por defecto**. El Context Gateway funciona sin hooks de proyecto.

```bash
io-delegation setup --project . --guard observe
io-delegation setup --project . --guard enforce
```

Si el archivo de hooks que habría que modificar ya está trackeado por Git, setup se niega. Sólo se puede continuar después de revisar el diff con:

```bash
io-delegation setup --project . --guard observe --allow-tracked-config
```

`observe` registra pero no bloquea; `enforce` rechaza lecturas cubiertas que superan el presupuesto.

## Scope

Sin argumentos, setup detecta carpetas fuente y archivos de texto de raíz, excluyendo dependencias, builds, caches, agentes y patrones sensibles.

Para definirlo explícitamente:

```bash
io-delegation setup --project . \
  --allow-prefix src \
  --allow-prefix tests \
  --allow-file package.json
```

## Estado y diagnóstico

```bash
io-delegation status --project .
io-delegation doctor --project .
```

`doctor` verifica marcador, runtime, registro MCP, skill, audit, configs de routing/orchestration/compaction y un handshake MCP real con `tools/list`. No llama a Jev ni al worker.

Una credencial configurada pero ausente se reporta como warning. Si Claude Code no está instalado, su registro user-scope queda pendiente y también se informa como warning.

## Desinstalación

Previsualizar primero:

```bash
io-delegation remove --project . --dry-run
```

Aplicar:

```bash
io-delegation remove --project .
```

La eliminación quita únicamente el proyecto del estado y las entradas MCP administradas que ya no necesite ningún otro proyecto. Si otro proyecto sigue usando I/O Delegation, el MCP global permanece.

Una skill modificada por el usuario se conserva; `--force` es necesario para eliminarla. Auditorías y backups se preservan salvo `--purge-data`. El runtime compartido se conserva salvo `--purge-runtime` y sólo se elimina cuando no quedan proyectos activos.

## Backups y restore

Cada cambio a configuración global crea un snapshot con hashes antes/después.

```bash
io-delegation backups
io-delegation restore ID --dry-run
io-delegation restore ID
```

Restore automático sólo procede si el archivo objetivo sigue exactamente como lo dejó I/O Delegation. Si el usuario lo modificó después, se detiene para evitar borrar cambios. `--force` existe únicamente para una restauración revisada explícitamente.

## Colisiones de MCP

Setup nunca reemplaza silenciosamente un `io_context` que no reconoce como propio. Después de revisar el servidor existente se puede usar:

```bash
io-delegation setup --project . --replace-existing-mcp
```

## Portabilidad

No se recomienda commitear rutas de máquina. La instalación V1.1 evita escribirlas en configs MCP del proyecto:

- Codex: MCP global administrado
- Cursor: MCP global + `${workspaceFolder}`
- Claude Code: MCP user-scope
- proyecto: sólo skill + marcador privado ignorado

El runtime y las configuraciones privadas viven bajo `~/.io-delegation/` y no se publican con el repositorio receptor.
