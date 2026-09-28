"""Where wokitoki keeps its files (see docs/DESIGN.md, "Where things live").

- configuration (aliases.yaml, user libraries): platformdirs user_config_dir,
  overridable with WOKITOKI_CONFIG
- radio data (reads, backups): ~/wokitoki/<alias>/, overridable with WOKITOKI_HOME
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "wokitoki"


def config_dir() -> Path:
    override = os.environ.get("WOKITOKI_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def aliases_file() -> Path:
    return config_dir() / "aliases.yaml"


def user_library_dir() -> Path:
    return config_dir() / "library"


def builtin_library_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "library"


def data_home() -> Path:
    override = os.environ.get("WOKITOKI_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / APP_NAME


def radio_dir(alias: str) -> Path:
    return data_home() / alias
