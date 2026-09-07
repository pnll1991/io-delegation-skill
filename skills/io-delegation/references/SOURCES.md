# Procedencia y decisiones de adaptación

Fuentes consultadas el 7 de septiembre de 2026. Implementación independiente: no es un producto oficial ni una copia del plugin de Spotify. No se redistribuyen el artículo, sus imágenes ni su código.

## Inspiración editorial

El artículo de Dimitri Mazmanov, publicado el 3 de septiembre de 2026, separa trabajo de I/O y razonamiento mediante dos auxiliares. Expone una implementación en Portal, resultados propios y limitaciones de edición, juicio y latencia. Los ejemplos usan Gemini 2.5 Flash y temperatura 0,2; no son requisitos universales. Sus mediciones no prueban el ahorro de este paquete. La proyección económica introductoria no se utiliza como evidencia de eficacia ni se verificó independientemente [S1].

[S1]: https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90

## Mecanismos de referencia

El README de `shunt` describe hooks de lectura, wrappers con argumentos nombrados y skills; consultas independientes; lector con límites entre archivos; generador con referencia obligatoria y salida a disco; umbral de 350 líneas, lecturas dirigidas exceptuadas y límites de payload/tiempo. Reconoce que el generador carece de enforcement por hooks [S2].

Portal documenta modos definidos por instrucciones y herramientas, con límites de alcance y configuración reutilizable. Esa configuración específica se reemplaza aquí por contratos y adaptadores explícitos [S3].

[S2]: https://github.com/sorantis/portal-ai-plugins/tree/add-shunt-claude/plugins/shunt
[S3]: https://backstage.spotify.com/docs/portal/core-features-and-plugins/aika/modes

## Correspondencia del enfoque con esta skill

| Área de la referencia | Resolución en esta implementación |
| --- | --- |
| Problema de exceso de I/O | Clasificación previa; búsqueda determinista antes de llamar a un modelo |
| Dos perfiles especializados | `bulk-read` factual y `code-write` para archivos nuevos |
| Configuración separada del enrutamiento | Política en `SKILL.md`; transporte en JSON aprobado |
| Capas de control | Instrucciones portables + validadores de CLI; sin prometer hooks universales |
| Reutilización entre proyectos | Carpeta autocontenida, instalación local/global y referencias relativas |
| Selección de archivos | Allowlist explícita; JSON serializado en lugar de delimitadores XML |
| Salida compacta | Resumen validado por evidencia o manifiesto de candidato |
| Repreguntas independientes | Sin historial principal; reconocer uso adicional del auxiliar |
| Lecturas dirigidas y edición | Reabrir el original vigente; editar desde el principal |
| Generación basada en patrones | Referencia obligatoria, API pertinente y aceptación definida |
| Latencia y límites | Presupuesto configurable de llamada; partición explícita; no copiar el límite de Portal como universal |
| Benchmark | Procedimiento comparativo propio; ningún porcentaje heredado |
| Extensibilidad | Nuevos contratos acotados, sin obligación de usar MCP o plugins |
| Adopción | Instalación por carpeta, no marketplace obligatorio |

La tabla documenta decisiones del paquete, no equivalencia funcional con Portal. No se implementan su administración, autenticación, resolución de modos, permisos de equipo ni servicios alojados.

## Cambios deliberados y motivo

El lector devuelve JSON con evidencia literal verificable, en vez de confiar en números de línea del modelo. La coincidencia se localiza sobre un snapshot y se adjunta un hash; aún requiere juicio semántico del principal.

El generador crea candidatos, no archivos activos. Se privilegia revisión y reversibilidad frente al ahorro máximo de contexto. Una ambigüedad de negocio se escala en lugar de completarse por analogía.

El fallback no necesita un segundo modelo. Los casos baratos se resuelven con herramientas; no se fuerza una llamada para demostrar uso de la skill. Tampoco se promete retención cero para transportes ajenos.

El script usa stdin para el adaptador de comando, limita tamaños, rechaza evidencias inexistentes y cambios del corpus, separa métricas y resultado, y no reintenta ni cambia de proveedor automáticamente. Estos controles son aportes de esta implementación, no controles atribuidos al artículo.

## Formato e integración

El formato `SKILL.md` con `name` y `description`, recursos relativos y carga progresiva sigue Agent Skills [S4]. Se evita `allowed-tools`, sustituciones de variables del anfitrión, hooks y frontmatter específico de proveedor.

Las rutas de instalación se verificaron en documentación oficial: `.claude/skills` para Claude Code; `.agents/skills` para Codex; Cursor reconoce `.agents/skills` y `.cursor/skills`, además de rutas de compatibilidad [S5][S6][S7]. Una ruta documentada no demuestra que se haya ensayado aquí la versión instalada de cada agente.

[S4]: https://agentskills.io/specification
[S5]: https://code.claude.com/docs/en/skills
[S6]: https://developers.openai.com/codex/skills
[S7]: https://cursor.com/docs/skills

## Referencias completas

- [Artículo de Spotify Engineering][S1]
- [README del plugin enlazado por el artículo][S2]
- [Documentación de AiKA Modes][S3]
- [Especificación Agent Skills][S4]
- [Skills en Claude Code][S5]
- [Skills en Codex][S6]
- [Skills en Cursor][S7]

Los nombres y marcas pertenecen a sus respectivos titulares. La licencia MIT de este repositorio cubre únicamente su material original.
