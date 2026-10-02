"""The upstream MCP server (``laya.mcp.server``, laya 0.3.23): its eight tools, frozen.

The platform reuses this stdio server as is (no ``platform_*`` tools in this phase). Its tools are
listed from the server object itself -- the same list an MCP client receives -- with the JSON type
of every argument and which ones are required. Nothing is called, so no checkpoint is built.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import laya.mcp.server as mcp_server
import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "laya_platform"

_NULLABLE_STR = ["null", "string"]
_NULLABLE_INT = ["integer", "null"]
_NULLABLE_NUM = ["null", "number"]
TOOLS: dict[str, tuple[dict[str, Any], list[str]]] = {
    "laya_status": ({}, []),
    "laya_route": (
        {
            "state": "object",
            "questions": "object",
            "model": _NULLABLE_STR,
            "task": _NULLABLE_STR,
            "lang": _NULLABLE_STR,
            "lang_guess": _NULLABLE_STR,
        },
        ["state", "questions"],
    ),
    "laya_predict": (
        {
            "state": "object",
            "questions": "object",
            "model": "string",
            "task": _NULLABLE_STR,
            "lang": _NULLABLE_STR,
            "lang_guess": _NULLABLE_STR,
            "max_len": _NULLABLE_INT,
            "head_max_len": _NULLABLE_INT,
            "min_confidence": _NULLABLE_NUM,
        },
        ["state", "questions"],
    ),
    "laya_predict_batch": (
        {
            "requests": "array",
            "batch_size": "integer",
            "hooks_timeout": "number",
            "min_confidence": "number",
            "sort_by_length": "boolean",
        },
        ["requests"],
    ),
    "laya_route_batch": ({"requests": "array", "hooks_timeout": "number"}, ["requests"]),
    "laya_shortlist": (
        {
            "state": "object",
            "questions": "object",
            "model": "string",
            "k": "integer",
            "task": _NULLABLE_STR,
            "lang": _NULLABLE_STR,
            "lang_guess": _NULLABLE_STR,
            "max_len": _NULLABLE_INT,
            "head_max_len": _NULLABLE_INT,
            "min_confidence": _NULLABLE_NUM,
        },
        ["state", "questions"],
    ),
    "laya_preset": (
        {
            "preset": "string",
            "state": "object",
            "task": _NULLABLE_STR,
            "lang": _NULLABLE_STR,
            "lang_guess": _NULLABLE_STR,
            "max_len": _NULLABLE_INT,
            "head_max_len": _NULLABLE_INT,
            "min_confidence": _NULLABLE_NUM,
        },
        ["preset", "state"],
    ),
    "laya_decide": (
        {"state": "object", "schema": "object", "model": "string", "min_confidence": _NULLABLE_NUM},
        ["state", "schema"],
    ),
}


def _json_type(schema: dict[str, Any]) -> Any:
    if "type" in schema:
        return schema["type"]
    return sorted(branch.get("type", "?") for branch in schema.get("anyOf", []))


@pytest.fixture(scope="module")
def listed() -> dict[str, dict[str, Any]]:
    tools = asyncio.run(mcp_server.server.list_tools())
    return {tool.name: tool.model_dump() for tool in tools}


def test_exactly_the_eight_upstream_tools(listed: dict[str, dict[str, Any]]) -> None:
    assert sorted(listed) == sorted(TOOLS)
    assert not [name for name in listed if name.startswith("platform_")]


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_tool_arguments(listed: dict[str, dict[str, Any]], name: str) -> None:
    schema = listed[name]["input_schema"]
    properties = schema.get("properties", {})
    arguments, required = TOOLS[name]
    assert {key: _json_type(value) for key, value in properties.items()} == arguments
    assert sorted(schema.get("required", [])) == sorted(required)
    assert listed[name]["description"]


def test_the_server_is_stdio_and_builds_nothing_on_import() -> None:
    assert mcp_server._ROUTER is None  # the Router is built on first call, not on import
    assert callable(mcp_server.main)


def test_the_platform_adds_no_mcp_tools_yet() -> None:
    # Administrative platform_* tools are F10; until then nothing in src/ registers a tool.
    sources = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    assert "platform_" not in sources
    assert ".tool(" not in sources
