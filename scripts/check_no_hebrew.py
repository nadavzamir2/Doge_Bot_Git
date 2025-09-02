#!/usr/bin/env python3
"""Fail if any project (source) files contain Hebrew characters.

Scans for Unicode codepoints in the Hebrew block \u0590-\u05FF.

Excludes typical non-source / generated / vendor dirs to avoid noise.
Set ALLOW_HEBREW=1 to bypass (useful for emergency hotfix commits).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HEBREW_RE = re.compile(r"[\u0590-\u05FF]")

ROOT = Path(__file__).resolve().parent.parent  # repo root

EXCLUDE_DIRS = {
    '.git', 'venv', 'venv.bak', '__pycache__', 'logs', 'data',
}

EXCLUDE_PREFIXES = (
    'venv.bak_',  # pattern for archived venvs
)

INCLUDE_EXTS = {
    '.py', '.sh', '.env', '.txt', '.md', '.yml', '.yaml', '.json', '.cfg', '.ini'
}

def should_skip_dir(dir_name: str) -> bool:
    if dir_name in EXCLUDE_DIRS:
        return True
    return any(dir_name.startswith(p) for p in EXCLUDE_PREFIXES)

def scan() -> list[str]:
    matches: list[str] = []
    for path in ROOT.rglob('*'):
        if path.is_dir():
            # handled implicitly by filtering when descending
            continue
        if path.is_symlink():
            continue
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if any(should_skip_dir(p) for p in parts[:-1]):
            continue
        # Only inspect selected extensions (or executable scripts without extension)
        if path.suffix == '' and os.access(path, os.X_OK):
            inspect = True
        else:
            inspect = path.suffix.lower() in INCLUDE_EXTS
        if not inspect:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        if not HEBREW_RE.search(text):
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if HEBREW_RE.search(line):
                snippet = line.strip()
                if len(snippet) > 100:
                    snippet = snippet[:100] + '…'
                matches.append(f"{rel}:{idx}: {snippet}")
    return matches

def main() -> int:
    if os.getenv('ALLOW_HEBREW') == '1':
        print('[hebrew-scan] Skipped (ALLOW_HEBREW=1)')
        return 0
    matches = scan()
    if matches:
        print('Hebrew characters detected in source files:')
        for m in matches:
            print('  ' + m)
        print('\nFailing build. Remove or translate these occurrences, or set ALLOW_HEBREW=1 to override.')
        return 1
    print('[hebrew-scan] OK (no Hebrew characters found).')
    return 0

if __name__ == '__main__':
    sys.exit(main())
