"""Small dependency-free evaluator for the JSON Schema keywords used here.

This is not exposed as a general JSON Schema implementation. It exists so the
standalone connector conformance runner validates its own golden wire values
without adding a runtime or development dependency.

Draft 2020-12 considers finite zero-fraction JSON numbers integers. Canonical
runtime APIs may still deliberately require exact Python `int` values.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when a conformance value violates a materialized schema."""


def _fragment(document: Any, pointer: str, *, label: str) -> Any:
    current = document
    if not pointer:
        return current
    if not pointer.startswith("/"):
        raise SchemaValidationError(f"{label} has an unsupported fragment")
    for encoded in pointer[1:].split("/"):
        component = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or component not in current:
            raise SchemaValidationError(f"{label} points to missing component {component!r}")
        current = current[component]
    return current


def _resolve(
    reference: str,
    *,
    current_name: str,
    documents: dict[str, dict[str, Any]],
) -> tuple[Any, str]:
    target_name, _, pointer = reference.partition("#")
    target_name = target_name or current_name
    if "://" in target_name or target_name not in documents:
        raise SchemaValidationError(f"{current_name} has unresolved local ref {reference!r}")
    return (
        _fragment(
            documents[target_name],
            pointer,
            label=f"{current_name}:{reference}",
        ),
        target_name,
    )


_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "title",
        "description",
        "format",
        "type",
        "const",
        "enum",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "required",
        "properties",
        "additionalProperties",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "minItems",
        "maxItems",
        "uniqueItems",
        "items",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)
_SCHEMA_MAP_KEYWORDS = ("$defs", "properties")
_SCHEMA_LIST_KEYWORDS = ("allOf", "anyOf", "oneOf")
_SCHEMA_VALUE_KEYWORDS = (
    "not",
    "if",
    "then",
    "else",
    "additionalProperties",
    "propertyNames",
    "items",
)
_SCHEMA_TYPES = frozenset(
    {"null", "boolean", "integer", "number", "string", "array", "object"}
)
_NONNEGATIVE_INTEGER_KEYWORDS = (
    "minProperties",
    "maxProperties",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
)
_NUMERIC_KEYWORDS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
)
_STRING_METADATA_KEYWORDS = ("$schema", "$id", "title", "description")
_RFC3339_DATE_TIME = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"[Tt](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.[0-9]+)?"
    r"(?P<zone>[Zz]|(?P<offset_sign>[+-])(?P<offset_hour>[0-9]{2}):"
    r"(?P<offset_minute>[0-9]{2}))\Z"
)
_PUBLISHED_LEAP_SECOND_DATES = frozenset(
    {
        (1972, 6, 30),
        (1972, 12, 31),
        (1973, 12, 31),
        (1974, 12, 31),
        (1975, 12, 31),
        (1976, 12, 31),
        (1977, 12, 31),
        (1978, 12, 31),
        (1979, 12, 31),
        (1981, 6, 30),
        (1982, 6, 30),
        (1983, 6, 30),
        (1985, 6, 30),
        (1987, 12, 31),
        (1989, 12, 31),
        (1990, 12, 31),
        (1992, 6, 30),
        (1993, 6, 30),
        (1994, 6, 30),
        (1995, 12, 31),
        (1997, 6, 30),
        (1998, 12, 31),
        (2005, 12, 31),
        (2008, 12, 31),
        (2012, 6, 30),
        (2015, 6, 30),
        (2016, 12, 31),
    }
)


def _valid_rfc3339_date_time(value: str) -> bool:
    match = _RFC3339_DATE_TIME.fullmatch(value)
    if match is None:
        return False
    year = int(match["year"])
    month = int(match["month"])
    day = int(match["day"])
    hour = int(match["hour"])
    minute = int(match["minute"])
    second = int(match["second"])
    offset_hour = int(match["offset_hour"] or 0)
    offset_minute = int(match["offset_minute"] or 0)
    if hour > 23 or minute > 59 or second > 60:
        return False
    if offset_hour > 23 or offset_minute > 59:
        return False
    try:
        local = datetime(year, month, day, hour, minute, min(second, 59))
    except ValueError:
        return False
    if second < 60:
        return True
    offset = offset_hour * 60 + offset_minute
    if match["offset_sign"] == "-":
        offset = -offset
    utc = local - timedelta(minutes=offset)
    return (
        (utc.year, utc.month, utc.day) in _PUBLISHED_LEAP_SECOND_DATES
        and utc.hour == 23
        and utc.minute == 59
    )


def audit_schema_documents(
    schema_names: set[str] | frozenset[str],
    *,
    documents: dict[str, dict[str, Any]],
) -> None:
    """Reject malformed or unsupported schemas before evaluating instances."""

    visited: set[tuple[str, int]] = set()
    active: set[tuple[str, int]] = set()

    def audit(schema: Any, *, current_name: str, path: str) -> None:
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise SchemaValidationError(f"{path} is not a schema object")
        identity = (current_name, id(schema))
        if identity in active:
            raise SchemaValidationError(
                f"{path} resolves an unsupported cyclic schema reference"
            )
        if identity in visited:
            return
        active.add(identity)
        try:
            unknown = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYWORDS)
            if unknown:
                raise SchemaValidationError(
                    f"{path} uses unsupported schema keywords {unknown!r}"
                )
            for keyword in _STRING_METADATA_KEYWORDS:
                if keyword in schema and not isinstance(schema[keyword], str):
                    raise SchemaValidationError(f"{path}.{keyword} must be a string")
            reference = schema.get("$ref")
            if reference is not None and not isinstance(reference, str):
                raise SchemaValidationError(f"{path} has a non-string $ref")

            expected_type = schema.get("type")
            if expected_type is not None:
                choices = (
                    [expected_type]
                    if isinstance(expected_type, str)
                    else expected_type
                )
                if (
                    not isinstance(choices, list)
                    or not choices
                    or not all(
                        isinstance(choice, str) and choice in _SCHEMA_TYPES
                        for choice in choices
                    )
                    or len(set(choices)) != len(choices)
                ):
                    raise SchemaValidationError(f"{path}.type is invalid")

            if "enum" in schema:
                choices = schema["enum"]
                if not isinstance(choices, list) or not choices:
                    raise SchemaValidationError(f"{path}.enum must be a non-empty array")
                for index, choice in enumerate(choices):
                    if any(_json_equal(choice, previous) for previous in choices[:index]):
                        raise SchemaValidationError(
                            f"{path}.enum must contain unique JSON values"
                        )

            required = schema.get("required")
            if required is not None and (
                not isinstance(required, list)
                or not all(isinstance(name, str) for name in required)
                or len(set(required)) != len(required)
            ):
                raise SchemaValidationError(
                    f"{path}.required must be an array of unique strings"
                )

            if "format" in schema:
                value = schema["format"]
                if not isinstance(value, str):
                    raise SchemaValidationError(f"{path}.format must be a string")
                if value != "date-time":
                    raise SchemaValidationError(
                        f"{path} uses unsupported schema format {value!r}"
                    )
            if "pattern" in schema:
                pattern = schema["pattern"]
                if not isinstance(pattern, str):
                    raise SchemaValidationError(f"{path}.pattern must be a string")
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise SchemaValidationError(
                        f"{path}.pattern is not a supported regular expression"
                    ) from exc

            for keyword in _NONNEGATIVE_INTEGER_KEYWORDS:
                if keyword not in schema:
                    continue
                value = schema[keyword]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise SchemaValidationError(
                        f"{path}.{keyword} must be a non-negative integer"
                    )
            for keyword in _NUMERIC_KEYWORDS:
                if keyword not in schema:
                    continue
                value = schema[keyword]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or (isinstance(value, float) and not math.isfinite(value))
                ):
                    raise SchemaValidationError(
                        f"{path}.{keyword} must be a finite number"
                    )
            if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
                raise SchemaValidationError(f"{path}.uniqueItems must be a boolean")

            for keyword in _SCHEMA_MAP_KEYWORDS:
                if keyword not in schema:
                    continue
                values = schema[keyword]
                if not isinstance(values, dict):
                    raise SchemaValidationError(f"{path}.{keyword} must be an object")
                if not all(isinstance(name, str) for name in values):
                    raise SchemaValidationError(
                        f"{path}.{keyword} names must be strings"
                    )
                for name, subschema in values.items():
                    audit(
                        subschema,
                        current_name=current_name,
                        path=f"{path}.{keyword}.{name}",
                    )
            for keyword in _SCHEMA_LIST_KEYWORDS:
                if keyword not in schema:
                    continue
                values = schema[keyword]
                if not isinstance(values, list) or not values:
                    raise SchemaValidationError(
                        f"{path}.{keyword} must be a non-empty array"
                    )
                for index, subschema in enumerate(values):
                    audit(
                        subschema,
                        current_name=current_name,
                        path=f"{path}.{keyword}[{index}]",
                    )
            for keyword in _SCHEMA_VALUE_KEYWORDS:
                if keyword in schema and isinstance(schema[keyword], (bool, dict)):
                    audit(
                        schema[keyword],
                        current_name=current_name,
                        path=f"{path}.{keyword}",
                    )
                elif keyword in schema:
                    raise SchemaValidationError(f"{path}.{keyword} is not a schema")
            if reference is not None:
                target, target_name = _resolve(
                    reference,
                    current_name=current_name,
                    documents=documents,
                )
                audit(target, current_name=target_name, path=f"{path}.$ref")
        finally:
            active.remove(identity)
        visited.add(identity)

    for schema_name in sorted(schema_names):
        if schema_name not in documents:
            raise SchemaValidationError(f"unknown schema document {schema_name!r}")
        audit(documents[schema_name], current_name=schema_name, path=schema_name)

def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(_json_equal(a, b) for a, b in zip(left, right, strict=True))
        )
    if isinstance(left, dict) or isinstance(right, dict):
        return (
            isinstance(left, dict)
            and isinstance(right, dict)
            and set(left) == set(right)
            and all(_json_equal(left[key], right[key]) for key in left)
        )
    return False


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        if isinstance(instance, int) and not isinstance(instance, bool):
            return True
        return (
            isinstance(instance, float)
            and math.isfinite(instance)
            and instance.is_integer()
        )
    if expected == "number":
        if isinstance(instance, int) and not isinstance(instance, bool):
            return True
        return isinstance(instance, float) and math.isfinite(instance)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    raise SchemaValidationError(f"unsupported schema type {expected!r}")


def _valid(
    instance: Any,
    schema: Any,
    *,
    current_name: str,
    documents: dict[str, dict[str, Any]],
) -> bool:
    try:
        _validate(
            instance,
            schema,
            current_name=current_name,
            documents=documents,
            path="$",
        )
    except SchemaValidationError:
        return False
    return True


def _validate(
    instance: Any,
    schema: Any,
    *,
    current_name: str,
    documents: dict[str, dict[str, Any]],
    path: str,
) -> None:
    if isinstance(instance, float) and not math.isfinite(instance):
        raise SchemaValidationError(f"{path} is not a finite JSON number")
    if schema is True:
        return
    if schema is False:
        raise SchemaValidationError(f"{path} is forbidden by the schema")
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{current_name} contains a non-object schema")

    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str):
            raise SchemaValidationError(f"{current_name} has a non-string $ref")
        target, target_name = _resolve(
            reference,
            current_name=current_name,
            documents=documents,
        )
        _validate(
            instance,
            target,
            current_name=target_name,
            documents=documents,
            path=path,
        )

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(choices, list) or not all(isinstance(choice, str) for choice in choices):
            raise SchemaValidationError(f"{current_name} has an invalid type keyword")
        if not any(_type_matches(instance, choice) for choice in choices):
            raise SchemaValidationError(f"{path} does not have required type {expected_type!r}")

    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise SchemaValidationError(f"{path} does not equal the required constant")
    if "enum" in schema and not any(_json_equal(instance, choice) for choice in schema["enum"]):
        raise SchemaValidationError(f"{path} is not in the required enumeration")

    for subschema in schema.get("allOf", []):
        _validate(
            instance,
            subschema,
            current_name=current_name,
            documents=documents,
            path=path,
        )
    if "anyOf" in schema:
        matches = sum(
            _valid(
                instance,
                subschema,
                current_name=current_name,
                documents=documents,
            )
            for subschema in schema["anyOf"]
        )
        if matches == 0:
            raise SchemaValidationError(f"{path} does not match any anyOf branch")
    if "oneOf" in schema:
        matches = sum(
            _valid(
                instance,
                subschema,
                current_name=current_name,
                documents=documents,
            )
            for subschema in schema["oneOf"]
        )
        if matches != 1:
            raise SchemaValidationError(
                f"{path} matches {matches} oneOf branches instead of exactly one"
            )
    if "not" in schema and _valid(
        instance,
        schema["not"],
        current_name=current_name,
        documents=documents,
    ):
        raise SchemaValidationError(f"{path} matches a forbidden schema")
    if "if" in schema:
        branch = (
            "then"
            if _valid(
                instance,
                schema["if"],
                current_name=current_name,
                documents=documents,
            )
            else "else"
        )
        if branch in schema:
            _validate(
                instance,
                schema[branch],
                current_name=current_name,
                documents=documents,
                path=path,
            )

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            raise SchemaValidationError(f"{path} is missing fields {missing!r}")
        properties = schema.get("properties", {})
        for name, subschema in properties.items():
            if name in instance:
                _validate(
                    instance[name],
                    subschema,
                    current_name=current_name,
                    documents=documents,
                    path=f"{path}.{name}",
                )
        unknown = set(instance) - set(properties)
        additional = schema.get("additionalProperties", True)
        if additional is False and unknown:
            raise SchemaValidationError(f"{path} contains unknown fields {sorted(unknown)!r}")
        if isinstance(additional, dict):
            for name in unknown:
                _validate(
                    instance[name],
                    additional,
                    current_name=current_name,
                    documents=documents,
                    path=f"{path}.{name}",
                )
        if "propertyNames" in schema:
            for name in instance:
                _validate(
                    name,
                    schema["propertyNames"],
                    current_name=current_name,
                    documents=documents,
                    path=f"{path}.<property>",
                )
        if len(instance) < schema.get("minProperties", 0):
            raise SchemaValidationError(f"{path} has too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaValidationError(f"{path} has too many properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise SchemaValidationError(f"{path} has too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(f"{path} has too many items")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(instance):
                if any(_json_equal(item, previous) for previous in instance[:index]):
                    raise SchemaValidationError(f"{path} has duplicate items")
        if "items" in schema:
            for index, item in enumerate(instance):
                _validate(
                    item,
                    schema["items"],
                    current_name=current_name,
                    documents=documents,
                    path=f"{path}[{index}]",
                )

    if isinstance(instance, str):
        if schema.get("format") == "date-time" and not _valid_rfc3339_date_time(instance):
            raise SchemaValidationError(f"{path} is not a supported date-time")
        if len(instance) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{path} is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError(f"{path} is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise SchemaValidationError(f"{path} does not match the required pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path} is below the minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path} is above the maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise SchemaValidationError(f"{path} is not above the exclusive minimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise SchemaValidationError(f"{path} is not below the exclusive maximum")


def validate_instance(
    instance: Any,
    schema_name: str,
    *,
    documents: dict[str, dict[str, Any]],
) -> None:
    """Validate one JSON-shaped value against the repository schema graph."""

    if schema_name not in documents:
        raise SchemaValidationError(f"unknown schema document {schema_name!r}")
    audit_schema_documents(frozenset({schema_name}), documents=documents)
    try:
        _validate(
            instance,
            documents[schema_name],
            current_name=schema_name,
            documents=documents,
            path="$",
        )
    except RecursionError as exc:
        raise SchemaValidationError(
            f"{schema_name} exceeds supported schema recursion"
        ) from exc
