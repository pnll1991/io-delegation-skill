# Changelog

## Unreleased — Context Gateway V1 beta

The public MCP surface is `search`, `extract`, `query`. TypeSafe Jev remains optional and receives task text plus aggregate metadata, not source contents or file names.

The isolated semantic-worker benchmark did not support default auto-dispatch: it showed no material principal-token compression, roughly doubled principal+worker tokens, added material latency, and produced one clean paired quality regression. Worker auto-dispatch is therefore off by default and requires the explicit experimental `context_auto_dispatch: true` opt-in in an approved worker configuration. Historical benchmark targets remain frozen rather than being rewritten after observing the result.

## 0.2.0 — 2026-09-08

Added a provider-neutral read-budget engine and optional project-local pre-tool hook integrations for Claude Code, Codex and Cursor. The normal skill installer remains opt-in-free; hook registration requires a separate explicit command.

The gate measures line and byte budgets, bounded native ranges, recognized literal shell reads and aggregate batches. It supports observe/enforce modes, explicit coverage reporting and no-worker alternatives. It makes no model calls. Unknown tools and arbitrary programs are not claimed covered; this is not a sandbox or session-wide token cap.

Added a preserving, idempotent hook installer with runtime preflight, configuration backups, removal, dry-run and safeguards against overwriting modified or symlinked files. Added 51 automated gate/protocol/installer tests and a real-host verification checklist. Kept English as the primary README and updated the Spanish version.

Historical 0.1 token measurements and quality failures remain unchanged. They are explicitly distinguished from 0.2 enforcement, for which no autonomous-client or end-to-end token benchmark has been completed.

## 0.1.0 — 2026-09-07

Primera publicación de la skill portable: playbook, adaptadores opcionales, aislamiento de candidatos, verificación de evidencia y snapshots, instalador sin sobrescritura, 75 pruebas offline, metodología de benchmark y trazabilidad de fuentes.

Distribución pública en `pnll1991/io-delegation-skill`, README en inglés y español, guías de instalación, templates de contribución y workflow de pruebas multiplataforma.

La publicación inicial no incluía resultados de ahorro con modelos reales. Más tarde se documentaron el piloto controlado y el conteo de tokens de 0.1, conservando fallos de calidad y controles negativos; ver el README y benchmarks/README.md. El estado de CI y el alcance de validación se consultan por separado.
