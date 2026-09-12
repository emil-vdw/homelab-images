"""Validated routing configuration for the mail attachment importer."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from string import Formatter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class ConfigError(ValueError):
    """The configuration is unsafe or cannot be interpreted."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConfigError("mapping keys must be scalar values") from exc
        if duplicate:
            raise ConfigError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)

_MATCH_FIELDS = {
    "from_contains",
    "from_regex",
    "subject_contains",
    "subject_regex",
    "filename_contains",
    "filename_regex",
}
_TEMPLATE_FIELDS = {"year", "month", "date"}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Route:
    rule_id: str | None
    destination: str


@dataclass(frozen=True)
class Rule:
    id: str
    destination: str
    from_contains: str | None = None
    from_regex: str | None = None
    subject_contains: str | None = None
    subject_regex: str | None = None
    filename_contains: str | None = None
    filename_regex: str | None = None

    def matches(
        self, from_addresses: tuple[str, ...], subject: str, filename: str
    ) -> bool:
        sender_matches = not (self.from_contains or self.from_regex) or any(
            _matches(address, self.from_contains, self.from_regex)
            for address in from_addresses
        )
        return (
            sender_matches
            and _matches(subject, self.subject_contains, self.subject_regex)
            and _matches(filename, self.filename_contains, self.filename_regex)
        )


@dataclass(frozen=True)
class Source:
    id: str
    mailbox: str
    unmatched_destination: str
    rules: tuple[Rule, ...]

    def route(
        self,
        from_addresses: tuple[str, ...],
        subject: str,
        filename: str,
        when: datetime,
    ) -> Route:
        for rule in self.rules:
            if rule.matches(from_addresses, subject, filename):
                return Route(rule.id, render_destination(rule.destination, when))
        return Route(None, render_destination(self.unmatched_destination, when))


@dataclass(frozen=True)
class Config:
    timezone: ZoneInfo
    sources: tuple[Source, ...]


def _matches(value: str, contains: str | None, regex: str | None) -> bool:
    if contains is not None and contains.casefold() not in value.casefold():
        return False
    return regex is None or re.search(regex, value, re.IGNORECASE) is not None


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigError(f"{context} must be a mapping with string keys")
    return value


def _only_keys(value: Mapping[str, object], allowed: set[str], context: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"unknown {context} field(s): {', '.join(sorted(unknown))}")


def _required_string(value: Mapping[str, object], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    if _CONTROL_CHARACTERS.search(item):
        raise ConfigError(f"{context}.{key} contains a control character")
    return item


def _optional_string(value: Mapping[str, object], key: str, context: str) -> str | None:
    if key not in value:
        return None
    return _required_string(value, key, context)


def _validate_template(destination: str, context: str) -> None:
    try:
        pieces = tuple(Formatter().parse(destination))
    except ValueError as exc:
        raise ConfigError(f"{context} has invalid template syntax: {exc}") from exc
    for _, field, format_spec, conversion in pieces:
        if field is not None and field not in _TEMPLATE_FIELDS:
            raise ConfigError(f"{context} uses unsupported template field {field!r}")
        if format_spec or conversion:
            raise ConfigError(
                f"{context} does not support format specifications or conversions"
            )
    _validate_relative_path(destination, context, allow_templates=True)
    try:
        rendered = destination.format(year="2000", month="01", date="2000-01-01")
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"{context} has invalid template syntax: {exc}") from exc
    _validate_relative_path(rendered, context)


def _validate_relative_path(
    path: str, context: str, *, allow_templates: bool = False
) -> None:
    if path.startswith("/") or path.endswith("/") or "\\" in path:
        raise ConfigError(f"{context} must be a relative POSIX path")
    if _CONTROL_CHARACTERS.search(path):
        raise ConfigError(f"{context} contains a control character")
    parts = path.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ConfigError(f"{context} contains an unsafe path segment")
    if PurePosixPath(path).is_absolute():
        raise ConfigError(f"{context} must be relative")
    if not allow_templates and ("{" in path or "}" in path):
        raise ConfigError(f"{context} contains an unrendered template")


def render_destination(template: str, when: datetime) -> str:
    destination = template.format(
        year=f"{when.year:04d}",
        month=f"{when.month:02d}",
        date=when.date().isoformat(),
    )
    _validate_relative_path(destination, "rendered destination")
    return destination


def _parse_rule(raw: object, source_context: str, index: int) -> Rule:
    context = f"{source_context}.rules[{index}]"
    value = _mapping(raw, context)
    _only_keys(value, {"id", "destination"} | _MATCH_FIELDS, context)
    rule_id = _required_string(value, "id", context)
    destination = _required_string(value, "destination", context)
    _validate_template(destination, f"{context}.destination")
    matches = {
        field: _optional_string(value, field, context) for field in _MATCH_FIELDS
    }
    if not any(matches.values()):
        raise ConfigError(f"{context} must have at least one match condition")
    for field in ("from_regex", "subject_regex", "filename_regex"):
        pattern = matches[field]
        if pattern is not None:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ConfigError(f"{context}.{field} is invalid: {exc}") from exc
    return Rule(id=rule_id, destination=destination, **matches)


def _parse_source(raw: object, index: int) -> Source:
    context = f"sources[{index}]"
    value = _mapping(raw, context)
    _only_keys(value, {"id", "mailbox", "unmatched_destination", "rules"}, context)
    source_id = _required_string(value, "id", context)
    mailbox = _required_string(value, "mailbox", context)
    unmatched = _required_string(value, "unmatched_destination", context)
    _validate_template(unmatched, f"{context}.unmatched_destination")
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list):
        raise ConfigError(f"{context}.rules must be a list")
    rules = tuple(
        _parse_rule(rule, context, index) for index, rule in enumerate(raw_rules)
    )
    rule_ids = [rule.id for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ConfigError(f"{context} contains duplicate rule IDs")
    return Source(source_id, mailbox, unmatched, rules)


def parse_config(raw: object) -> Config:
    value = _mapping(raw, "configuration")
    _only_keys(value, {"timezone", "sources"}, "configuration")
    timezone_name = _required_string(value, "timezone", "configuration")
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"unknown timezone: {timezone_name}") from exc
    raw_sources = value.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("configuration.sources must be a non-empty list")
    sources = tuple(
        _parse_source(source, index) for index, source in enumerate(raw_sources)
    )
    ids = [source.id for source in sources]
    mailboxes = [source.mailbox for source in sources]
    if len(ids) != len(set(ids)):
        raise ConfigError("source IDs must be unique")
    if len(mailboxes) != len(set(mailboxes)):
        raise ConfigError("source mailbox names must be unique")
    return Config(timezone, sources)


def load_config(path: str | Path) -> Config:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            raw = yaml.load(stream, Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc
    return parse_config(raw)
