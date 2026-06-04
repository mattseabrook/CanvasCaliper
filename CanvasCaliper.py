from __future__ import annotations

import importlib.util
import os
import subprocess
import sys


PYSIDE_VERSION = "6.11.1"


def ensure_pyside6() -> None:
    if importlib.util.find_spec("PySide6") is not None:
        return
    print(f"PySide6 is not installed for {sys.executable}.")
    print(f"Installing PySide6=={PYSIDE_VERSION} into the current user Python...")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            f"PySide6=={PYSIDE_VERSION}",
        ]
    )


def main() -> int:
    ensure_pyside6()
    from canvascaliper.main import main as app_main

    return app_main()


if __name__ == "__main__":
    os.environ.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    raise SystemExit(main())
