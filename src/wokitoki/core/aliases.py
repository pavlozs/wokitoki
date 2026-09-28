"""Radio aliases stored in aliases.yaml (format: docs/FORMATS.md, section 1).

The file is meant to be edited by hand, so it is loaded and saved with
ruamel.yaml round-trip mode: user comments and key order survive.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from . import paths

ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
TRANSPORTS = ("ble", "serial")
FIELDS = ("driver", "transport", "address", "port", "name", "note", "added")

HEADER = "My radios. This file can be edited by hand (comments are preserved).\n"


class AliasError(Exception):
    pass


def validate_name(name: str) -> str:
    if not ALIAS_RE.match(name):
        raise AliasError(
            f"invalid alias '{name}': 1–32 characters a–z, 0–9, '-' or '_' (starting with a letter or digit)"
        )
    return name


@dataclass
class Alias:
    name: str
    driver: str
    transport: str
    address: str | None = None  # BLE: macOS UUID, Linux/Windows MAC
    port: str | None = None  # serial: COM3, /dev/ttyUSB0 …
    device_name: str | None = None  # what the device advertises
    note: str | None = None
    added: dt.date | None = None

    @property
    def target(self) -> str:
        return (self.address if self.transport == "ble" else self.port) or "?"

    def validate(self) -> None:
        validate_name(self.name)
        if self.transport not in TRANSPORTS:
            raise AliasError(f"{self.name}: unknown transport '{self.transport}' (ble, serial)")
        if self.transport == "ble" and not self.address:
            raise AliasError(f"{self.name}: a BLE alias needs 'address'")
        if self.transport == "serial" and not self.port:
            raise AliasError(f"{self.name}: a serial alias needs 'port'")

    @classmethod
    def from_yaml(cls, name: str, data) -> Alias:
        if not isinstance(data, dict):
            raise AliasError(f"alias '{name}': expected a mapping (key: value)")
        unknown = set(data) - set(FIELDS)
        if unknown:
            raise AliasError(f"alias '{name}': unknown keys {', '.join(sorted(map(str, unknown)))}")
        if "driver" not in data:
            raise AliasError(f"alias '{name}': missing 'driver'")
        added = data.get("added")
        if isinstance(added, str):
            try:
                added = dt.date.fromisoformat(added)
            except ValueError:
                added = None
        alias = cls(
            name=name,
            driver=str(data["driver"]),
            transport=str(data.get("transport", "ble")),
            address=_opt_str(data.get("address")),
            port=_opt_str(data.get("port")),
            device_name=_opt_str(data.get("name")),
            note=_opt_str(data.get("note")),
            added=added,
        )
        alias.validate()
        return alias

    def to_yaml(self) -> CommentedMap:
        out = CommentedMap()
        out["driver"] = _plain(self.driver)
        out["transport"] = _plain(self.transport)
        if self.address:
            out["address"] = _plain(self.address)
        if self.port:
            out["port"] = _plain(self.port)
        if self.device_name:
            out["name"] = DoubleQuotedScalarString(_plain(self.device_name))
        if self.note:
            out["note"] = DoubleQuotedScalarString(_plain(self.note))
        if self.added:
            out["added"] = self.added
        return out


def _plain(value: str) -> str:
    # ruamel.yaml looks up representers by exact type, so str subclasses
    # (e.g. pyobjc_unicode from bleak on macOS) cannot be dumped.
    return str.__str__(value) if type(value) is not str else value


def _opt_str(value) -> str | None:
    return None if value is None else _plain(str(value))


def _yaml() -> YAML:
    y = YAML()  # round-trip
    y.indent(mapping=2, sequence=4, offset=2)
    y.preserve_quotes = True
    return y


class AliasStore:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.aliases_file()
        self._doc = self._load()

    def _load(self) -> CommentedMap:
        if not self.path.exists():
            doc = CommentedMap()
            doc["aliases"] = CommentedMap()
            doc.yaml_set_start_comment(HEADER)
            return doc
        try:
            with self.path.open(encoding="utf-8") as f:
                doc = _yaml().load(f)
        except YAMLError as e:
            raise AliasError(f"{self.path} is not valid YAML: {e}") from None
        if doc is None:
            doc = CommentedMap()
        if not isinstance(doc, CommentedMap):
            raise AliasError(f"{self.path}: expected key 'aliases'")
        if doc.get("aliases") is None:
            doc["aliases"] = CommentedMap()
        if not isinstance(doc["aliases"], CommentedMap):
            raise AliasError(f"{self.path}: 'aliases' must be a mapping")
        for key in doc["aliases"]:
            if not isinstance(key, str):
                raise AliasError(f'{self.path}: alias {key!r} must be quoted, e.g. "{key}":')
        return doc

    @property
    def _aliases(self) -> CommentedMap:
        return self._doc["aliases"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                _yaml().dump(self._doc, f)
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def names(self) -> list[str]:
        return [str(k) for k in self._aliases]

    def __contains__(self, name: str) -> bool:
        return name in self._aliases

    def get(self, name: str) -> Alias:
        if name not in self._aliases:
            raise AliasError(f"alias '{name}' does not exist (see: wokitoki alias list)")
        return Alias.from_yaml(name, self._aliases[name])

    def all(self) -> list[Alias]:
        return [self.get(name) for name in self.names()]

    def add(self, alias: Alias, *, replace: bool = False) -> None:
        alias.validate()
        if alias.name in self._aliases and not replace:
            raise AliasError(f"alias '{alias.name}' already exists (overwrite: --force)")
        if alias.added is None:
            alias.added = dt.datetime.now().astimezone().date()
        self._aliases[alias.name] = alias.to_yaml()

    def remove(self, name: str) -> None:
        if name not in self._aliases:
            raise AliasError(f"alias '{name}' does not exist")
        del self._aliases[name]

    def rename(self, old: str, new: str) -> None:
        validate_name(new)
        if old not in self._aliases:
            raise AliasError(f"alias '{old}' does not exist")
        if new in self._aliases:
            raise AliasError(f"alias '{new}' already exists")
        position = list(self._aliases).index(old)
        value = self._aliases.pop(old)
        self._aliases.insert(position, new, value)
