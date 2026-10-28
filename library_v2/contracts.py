"""Local-only JSON Schema validation and deterministic content identity.

Schema references resolve from the installed package. No network lookup is
permitted, including for an accidental or malicious external reference.
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).with_name("schemas")
TOOLS = ("search_recipes", "get_recipes", "search_knowledge", "get_passages")
MAX_RESPONSE_BYTES = 131072


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def revision(record):
    return hashlib.sha256(canonical_json({k: v for k, v in record.items() if k != "revision"}).encode()).hexdigest()


@lru_cache(maxsize=1)
def _schemas():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    schemas = {p.name: json.loads(p.read_text()) for p in SCHEMA_DIR.glob("*.schema.json")}
    registry = Registry()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return schemas, registry


@lru_cache(maxsize=32)
def validator(name, definition=None):
    from jsonschema import Draft202012Validator
    schemas, registry = _schemas()
    schema = schemas[name]
    if definition:
        schema = {"$ref": schema["$id"] + "#/$defs/" + definition}
    return Draft202012Validator(schema, registry=registry)


def validate_content(record):
    definition = record.get("entity")
    if definition not in ("book", "recipe", "knowledge"):
        definition = "relationship" if "from" in record else "read_target" if "policy" in record and "targets" in record else "remapping"
    validator("content.schema.json", definition).validate(record)


def validate_input(tool, arguments):
    if tool not in TOOLS:
        raise ValueError("Unknown tool")
    validator(tool + ".input.schema.json").validate(arguments)


def validate_output(payload):
    validator("outputs.schema.json").validate(payload)
    if len(canonical_json(payload).encode()) > MAX_RESPONSE_BYTES:
        raise ValueError("Response exceeds serialized UTF-8 byte budget")


def validate_schemas():
    schemas, _ = _schemas()
    return sorted(schemas)
