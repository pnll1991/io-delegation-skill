#!/usr/bin/env python3
"""Instala una copia autocontenida sin modificar reglas ni configuraciones del agente."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=['claude-code', 'codex', 'cursor'], required=True)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--project', help='Directorio existente del proyecto.')
    scope.add_argument('--global', dest='global_scope', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent / 'skills' / 'io-delegation'
    try:
        base = Path.home() if args.global_scope else Path(args.project).resolve(strict=True)
        if not base.is_dir():
            raise ValueError('El proyecto debe ser un directorio.')
        # Codex y Cursor comparten una sola copia en .agents/skills.
        folder = '.claude' if args.agent == 'claude-code' else '.agents'
        target = base / folder / 'skills' / 'io-delegation'
        if target.exists() or target.is_symlink():
            raise FileExistsError('Ya existe la skill: no se sobrescribe. Revisá y respaldá la copia previa.')
        if not (source / 'SKILL.md').is_file():
            raise FileNotFoundError('Falta la carpeta fuente de la skill.')
        # No seguir destinos intermedios que apunten fuera del ámbito elegido.
        target.resolve().relative_to(base.resolve())
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print(json.dumps({'status': 'dry_run' if args.dry_run else 'installed',
                          'path': str(target), 'agent': args.agent}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
