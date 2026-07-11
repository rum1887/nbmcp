"""Generate MCP tool schemas from Python function signatures.

This runs once, at decoration time (when the module imports), not on every
call. The resulting JSON schema is handed to the Rust engine, which is what
actually validates incoming tool-call arguments.
"""

from __future__ import annotations

import inspect
import typing

_PRIMITIVE_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _annotation_to_schema(annotation) -> dict:
    if annotation is inspect.Signature.empty:
        # No type hint given: fall back to string, but this is a signal the
        # tool author should add one for a tighter schema.
        return {"type": "string"}

    origin = typing.get_origin(annotation)

    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _annotation_to_schema(args[0])
        return {"type": "string"}

    if origin in (list, typing.List):
        args = typing.get_args(annotation)
        item_schema = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}

    if origin in (dict, typing.Dict):
        return {"type": "object"}

    if annotation in _PRIMITIVE_TYPES:
        return {"type": _PRIMITIVE_TYPES[annotation]}

    # Unrecognized / complex annotation (custom class, etc.): don't guess.
    return {"type": "string"}


def _is_optional(annotation) -> bool:
    return (
        typing.get_origin(annotation) is typing.Union
        and type(None) in typing.get_args(annotation)
    )


def generate_tool_definition(func, description: str = "") -> dict:
    """Build an MCP tool definition {name, description, inputSchema} from a
    function's signature and type hints."""
    sig = inspect.signature(func)
    properties: dict[str, dict] = {}
    required: list[str] = []

    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        properties[pname] = _annotation_to_schema(param.annotation)
        has_default = param.default is not inspect.Signature.empty
        if not has_default and not _is_optional(param.annotation):
            required.append(pname)

    return {
        "name": func.__name__,
        "description": description or (inspect.getdoc(func) or ""),
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }
