# Changelog

## Unreleased — Context Gateway V1 beta

The public MCP surface is `search`, `extract`, `query`. TypeSafe Jev routing remains optional and receives task text plus aggregate metadata, not source contents or file names.

Added host-aware Jev model orchestration for approved Codex/Cursor CLI workers, then recalibrated it from live Codex evidence. Model control is now explicit: `manual` preserves a user-selected model, `suggest` is the default and only reports a recommendation, and `auto` is the opt-in that may switch approved profiles. The selector is monotonic (minimum sufficient profile), metadata-only `uncertainty_high` no longer increases model strength, balanced Codex starts at Luna high, Astra low requires multiple strong signals, and default profile retries are limited to one with a preset-specific cost-ratio guard. A missing-context signal falls back to the principal instead of buying a stronger model. The default Codex registry remains capped at Astra low; the default Cursor registry still excludes Astra. Users can replace registries/orders, lower ceilings, block profiles, bound escalations/cost jumps, or explicitly use a reviewed Cursor native-Router model string. Transport/config/budget failures return directly to the principal. `host-cli` reuses each host's existing authenticated CLI without copying credentials. No production savings claim is made from the live calibration sample. The legacy single `compute_profiles.cheap` path remains compatible.

Jev-guided conversation compaction for Claude Code, Codex and Cursor is now automatic by default for configured projects. Claude uses the vendored `tamaratran/fast-jev-compaction` core at pinned upstream commit `e3f262a7f4d42bd8dd32ced30d26176f7cb545b0` as a native `session.compact` replacement. Codex and Cursor use a stdlib Python bridge that journals tool I/O locally, asks Jev what exact tool evidence must survive native compaction, and rehydrates that evidence through each host's supported lifecycle hooks. The automatic compaction preference is independent from Jev routing; `--compaction off` is a persistent per-project escape hatch, while legacy project states with no stored preference migrate to automatic compaction on their next setup/update. Codex/Cursor never send full tool-result bodies to Jev; missing credentials or adapter failures remain fail-open to native compaction.

The isolated semantic-worker benchmark did not support unconditional default auto-dispatch: it showed no material principal-token compression, roughly doubled principal+worker tokens, added material latency, and produced one clean paired quality regression. That unconditional path therefore remains off by default and requires the explicit experimental `context_auto_dispatch: true` opt-in. Cheap-first compute orchestration is a different gated path and must be evaluated on its own paired evidence. Historical benchmark targets remain frozen rather than being rewritten after observing the result.

The repeated isolated Jev routing sample completed 20/20 pairs with equal aggregate quality (11/20 successes in each arm). Jev was actually called in 9 pairs. Those called pairs contained one quality gain and no clean quality regression; the only arm-level regression occurred where Jev was not called. The Jev arm still recorded 7 effective-route mismatches overall and the multi-file family never reached Jev. Normal setup therefore keeps Jev off even when `TYPESAFE_API_KEY` is present; explicit `--jev on`, `--jev auto`, and reviewed router configs remain available for experiments. Evidence is recorded in `benchmarks/v1-validation/evidence/jev-ab-20260918.json`.

## 0.2.0 — 2026-09-08

Added a provider-neutral read-budget engine and optional project-local pre-tool hook integrations for Claude Code, Codex and Cursor. The normal skill installer remains opt-in-free; hook registration requires a separate explicit command.

The gate measures line and byte budgets, bounded native ranges, recognized literal shell reads and aggregate batches. It supports observe/enforce modes, explicit coverage reporting and no-worker alternatives. It makes no model calls. Unknown tools and arbitrary programs are not claimed covered; this is not a sandbox or session-wide token cap.

Added a preserving, idempotent hook installer with runtime preflight, configuration backups, removal, dry-run and safeguards against overwriting modified or symlinked files. Added 51 automated gate/protocol/installer tests and a real-host verification checklist. Kept English as the primary README and updated the Spanish version.

Historical 0.1 token measurements and quality failures remain unchanged. They are explicitly distinguished from 0.2 enforcement, for which no autonomous-client or end-to-end token benchmark has been completed.

## 0.1.0 — 2026-09-07

Primera publicación de la skill portable: playbook, adaptadores opcionales, aislamiento de candidatos, verificación de evidencia y snapshots, instalador sin sobrescritura, 75 pruebas offline, metodología de benchmark y trazabilidad de fuentes.

Distribución pública en `pnll1991/io-delegation-skill`, README en inglés y español, guías de instalación, templates de contribución y workflow de pruebas multiplataforma.

La publicación inicial no incluía resultados de ahorro con modelos reales. Más tarde se documentaron el piloto controlado y el conteo de tokens de 0.1, conservando fallos de calidad y controles negativos; ver el README y benchmarks/README.md. El estado de CI y el alcance de validación se consultan por separado.
