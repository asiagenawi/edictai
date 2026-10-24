"""Write records in the repository's YAML style.

Block style, two-space indentation with list items indented under their key,
keys in the order given, and double quotes on strings that YAML would
otherwise read as dates or numbers (dates, years, session identifiers).
"""

from __future__ import annotations

import re
from typing import Any

import yaml

_AMBIGUOUS = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}|[-+]?\d+(?:\.\d+)?|\d{4}-\d{4}|yes|no|true|false|on|off|null|~)$",
    re.IGNORECASE,
)


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if _AMBIGUOUS.match(value) and not re.fullmatch(r"\d{4}-\d{4}", value):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _str)


def dump(record: dict[str, Any]) -> str:
    return yaml.dump(
        record,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
        indent=2,
    )
