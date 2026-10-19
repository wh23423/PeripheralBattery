"""PyInstaller entry point for the collector sidecar.

``collector/__main__.py`` uses package-relative imports (``from . import ...``),
so PyInstaller cannot execute it directly as a script: the bootloader runs the
entry file as top-level ``__main__`` and the relative imports fail with
"attempted relative import with no known parent package".

This wrapper imports the package module instead, keeping the package context.
It lives next to ``collector/`` so ``--paths <project root>`` finds it.

Dev (no packaging):  python -m collector --stdio
Packaged:            dist\\collector\\collector.exe --stdio   (scripts\\build-collector.ps1)
"""
from collector.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
