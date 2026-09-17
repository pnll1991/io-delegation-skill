# Instalación V1 — Context Gateway

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

El comando detecta Codex, Claude Code y Cursor cuando están disponibles. También se puede fijar uno o todos:

```bash
io-delegation setup --project . --agent codex
io-delegation setup --project . --agent all
```

La instalación es por proyecto. Copia la skill y registra un servidor MCP `io_context` con `search`, `extract` y `query`.

## Smart routing

Si `TYPESAFE_API_KEY` ya está disponible en el entorno, `--jev auto` (default) crea una configuración aprobada fuera del proyecto y registra solamente el nombre de la variable de entorno en los hosts.

```powershell
$env:TYPESAFE_API_KEY="..."
.\io-delegation.cmd setup --project .
```

También se puede controlar explícitamente:

```bash
io-delegation setup --project . --jev on
io-delegation setup --project . --jev off
```

`--jev on` exige que la key exista cuando `doctor` se ejecute; nunca escribe su valor. El proceso MCP/host hace la llamada a TypeSafe. El shell sandbox del agente no necesita acceso HTTP directo ni la key literal en archivos.

## Semantic worker

No hay worker por defecto. Para habilitarlo se entrega una configuración ya revisada y guardada fuera del proyecto:

```bash
io-delegation setup --project . --worker-config /ruta/privada/worker.json --update
```

## Read guard

El setup V1 usa `observe` por defecto: registra decisiones pero no bloquea lecturas.

```bash
io-delegation setup --project . --guard off
io-delegation setup --project . --guard observe
io-delegation setup --project . --guard enforce
```

`enforce` es opt-in y conviene activarlo después de verificar el host real.

## Scope

Si no se pasan rutas, setup detecta carpetas fuente y archivos de texto de nivel raíz. Excluye directorios de agentes, dependencias, builds, caches y patrones sensibles. Para control explícito:

```bash
io-delegation setup --project . \
  --allow-prefix src \
  --allow-prefix tests \
  --allow-file package.json
```

## Verificación

```bash
io-delegation status --project .
io-delegation doctor --project .
```

`doctor` ejecuta un handshake MCP real y `tools/list`, pero no llama a Jev ni al worker. Una key configurada pero ausente se reporta como warning.
