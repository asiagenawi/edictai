"""Controlled vocabularies from data/vocab/."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .yamlio import load_yaml

# Vocabularies with their own file; every other x-vocab name is a key in enums.yaml.
_FILE_VOCABS = {"topics": "topics.yaml", "provision_types": "provision-types.yaml"}


@dataclass
class Vocab:
    topics: list[dict[str, Any]]
    provision_types: list[dict[str, Any]]
    enums: dict[str, Any]
    _codes: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, vocab_dir: Path) -> Vocab:
        topics = load_yaml(vocab_dir / _FILE_VOCABS["topics"]) or []
        provision_types = load_yaml(vocab_dir / _FILE_VOCABS["provision_types"]) or []
        enums = load_yaml(vocab_dir / "enums.yaml") or {}
        return cls(topics=topics, provision_types=provision_types, enums=enums)

    def names(self) -> list[str]:
        return [*_FILE_VOCABS, *self.enums]

    def labels(self, name: str) -> dict[str, str]:
        """code -> human label for a vocabulary, in file order.

        Mapping-style enums carry a label per code; list-style enums do not,
        so the code doubles as its label.
        """
        if name in self._codes:
            return self._codes[name]
        if name == "topics":
            out = {t["code"]: t["name"] for t in self.topics}
        elif name == "provision_types":
            out = {p["code"]: p["name"] for p in self.provision_types}
        elif name in self.enums:
            value = self.enums[name]
            if isinstance(value, dict):
                out = {str(k): str(v) for k, v in value.items()}
            elif isinstance(value, list):
                out = {str(v): str(v) for v in value}
            else:
                raise ValueError(f"enums.yaml: {name} must be a mapping or a list")
        else:
            raise KeyError(name)
        self._codes[name] = out
        return out

    def codes(self, name: str) -> set[str]:
        return set(self.labels(name))

    def has(self, name: str) -> bool:
        return name in _FILE_VOCABS or name in self.enums
