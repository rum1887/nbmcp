"""Tests for nbmcp's Python-side schema generation (_schema.py)."""

import sys
import typing

sys.path.insert(0, "python")

from nbmcp._schema import generate_tool_definition


def test_primitive_types():
    """All primitive type hints map to the correct JSON Schema types."""

    def func(a: str, b: int, c: float, d: bool) -> None:
        pass

    tool_def = generate_tool_definition(func)
    props = tool_def["inputSchema"]["properties"]
    assert props["a"] == {"type": "string"}
    assert props["b"] == {"type": "integer"}
    assert props["c"] == {"type": "number"}
    assert props["d"] == {"type": "boolean"}


def test_required_fields():
    """Parameters without defaults are marked required."""

    def func(name: str, count: int, flag: bool = False) -> None:
        pass

    tool_def = generate_tool_definition(func)
    assert tool_def["inputSchema"]["required"] == ["name", "count"]


def test_optional_fields():
    """Optional[...] parameters are not required."""

    def func(name: typing.Optional[str] = None) -> None:
        pass

    tool_def = generate_tool_definition(func)
    assert "name" not in tool_def["inputSchema"]["required"]


def test_list_type():
    """list[T] generates an array schema with item type."""

    def func(items: typing.List[int]) -> None:
        pass

    tool_def = generate_tool_definition(func)
    assert tool_def["inputSchema"]["properties"]["items"] == {
        "type": "array",
        "items": {"type": "integer"},
    }


def test_dict_type():
    """dict generates an object schema."""

    def func(data: typing.Dict[str, int]) -> None:
        pass

    tool_def = generate_tool_definition(func)
    assert tool_def["inputSchema"]["properties"]["data"] == {"type": "object"}


def test_no_type_hint_falls_back_to_string():
    """Parameters without type hints default to string."""

    def func(name) -> None:
        pass

    tool_def = generate_tool_definition(func)
    assert tool_def["inputSchema"]["properties"]["name"] == {"type": "string"}


def test_description_from_decorator():
    """The description parameter is used as the tool description."""

    def func(city: str) -> None:
        pass

    tool_def = generate_tool_definition(func, description="Get weather")
    assert tool_def["description"] == "Get weather"


def test_description_from_docstring():
    """If no description is given, the docstring is used."""

    def func(city: str) -> None:
        """Get the current weather for a city."""
        pass

    tool_def = generate_tool_definition(func)
    assert tool_def["description"] == "Get the current weather for a city."


def test_self_parameter_skipped():
    """The 'self' parameter is excluded from the schema."""

    class MyClass:
        def method(self, city: str) -> None:
            pass

    tool_def = generate_tool_definition(MyClass.method)
    assert "self" not in tool_def["inputSchema"]["properties"]
    assert "city" in tool_def["inputSchema"]["properties"]


def test_union_types_fallback_to_string():
    """Union types (other than Optional) fall back to string."""

    def func(value: typing.Union[int, str]) -> None:
        pass

    tool_def = generate_tool_definition(func)
    assert tool_def["inputSchema"]["properties"]["value"] == {"type": "string"}