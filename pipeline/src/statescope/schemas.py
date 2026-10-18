"""JSON Schemas from data/schema/ and a registry that resolves their $refs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

BASE_KEY = "__base__"


@dataclass
class Schemas:
    by_kind: dict[str, dict[str, Any]]  # schema file stem -> schema
    registry: Registry
    common: dict[str, Any]

    @classmethod
    def load(cls, schema_dir: Path) -> Schemas:
        by_kind: dict[str, dict[str, Any]] = {}
        resources = []
        for path in sorted(schema_dir.glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            by_kind[path.name.removesuffix(".schema.json")] = schema
            resource = Resource.from_contents(schema, default_specification=DRAFT202012)
            resources.append((schema.get("$id", path.name), resource))
        common = by_kind.pop("common", {})
        registry = Registry().with_resources(resources)
        return cls(by_kind=by_kind, registry=registry, common=common)

    def resolve(self, schema: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
        """Follow a $ref (if any) to its target, merging sibling keywords over it.

        Used for documentation, where a property's description often sits beside
        the $ref that supplies its type. The result carries the schema its own
        local refs are relative to under BASE_KEY.
        """
        ref = schema.get("$ref")
        if not ref:
            return schema
        resolver = self.registry.resolver(base_uri=base.get("$id", ""))
        target = resolver.lookup(ref).contents
        owner = self._owner(ref, base)
        merged = {BASE_KEY: owner, **self.resolve(target, owner), **schema}
        merged.pop("$ref", None)
        return merged

    def _owner(self, ref: str, base: dict[str, Any]) -> dict[str, Any]:
        if ref.startswith("#"):
            return base
        return self.common if ref.startswith("common.schema.json") else base
