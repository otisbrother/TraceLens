"""Official MCP Python SDK client adapter for TraceLens.

Connects to `apps.mcp.server` over stdio (`StdioServerParameters`) or in-process
(`MCPServer`), always binding tool execution to a server-signed `TrustedContext`.
"""

import json
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from apps.api.config import settings
from apps.api.contracts import ToolEnvelope, TrustedContext
from apps.api.models import uid
from apps.api.tools import READONLY_TOOL_ALLOWLIST, issue_scope_token
from apps.mcp.server import build_mcp_server

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_stdio_params(database_engine) -> StdioServerParameters:
    db_url = database_engine.url.render_as_string(hide_password=False)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "apps.mcp.server"],
        env={
            "DATABASE_URL": db_url,
            "MCP_SIGNING_SECRET": settings.mcp_signing_secret,
            "PYTHONPATH": str(REPO_ROOT),
        },
    )


async def _async_list_tools(target) -> list[dict[str, Any]]:
    async with Client(target, read_timeout_seconds=30.0) as client:
        res = await client.list_tools()
        return [{"name": t.name, "description": t.description or ""} for t in res.tools]


def list_mcp_tools(database_engine, use_stdio: bool = True) -> list[dict[str, Any]]:
    target = _make_stdio_params(database_engine) if use_stdio else build_mcp_server(database_engine)
    return anyio.run(_async_list_tools, target)


async def _async_call_tool(
    target,
    ctx: TrustedContext,
    tool_name: str,
    raw_args: dict[str, Any],
) -> ToolEnvelope:
    if tool_name not in READONLY_TOOL_ALLOWLIST:
        return ToolEnvelope(
            request_id=uid(),
            source_system="mcp-client-guard",
            source_record_ids=[],
            as_of=ctx.cutoff,
            snapshot_id=ctx.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="TOOL_NOT_ALLOWED",
        )
    token = issue_scope_token(ctx)
    spec_known_keys = {
        "get_case_context": {"case_id"},
        "get_trip_history": {"entity_ids", "start_at", "end_at", "cursor", "page_size"},
        "get_device_usage_timeline": {"entity_ids", "start_at", "end_at"},
        "get_relationship_subgraph": {"entity_ids", "start_at", "end_at", "max_hops"},
        "get_promo_ledger": {"entity_ids", "start_at", "end_at"},
        "search_policy": {"query", "effective_at", "top_k"},
        "get_source_record": {"source_ref"},
    }[tool_name]
    known = {k: v for k, v in raw_args.items() if k in spec_known_keys}
    extra = {k: v for k, v in raw_args.items() if k not in spec_known_keys}
    call_payload: dict[str, Any] = {"scope_token": token, **known}
    if extra:
        call_payload["extra_args"] = extra

    async with Client(target, read_timeout_seconds=30.0) as client:
        result = await client.call_tool(tool_name, call_payload)
        text = result.content[0].text if result.content else "{}"
        parsed = json.loads(text)
        return ToolEnvelope.model_validate(parsed)


def execute_mcp_tool(
    database_engine,
    ctx: TrustedContext,
    tool_name: str,
    raw_args: dict[str, Any] | None = None,
    use_stdio: bool = True,
) -> ToolEnvelope:
    raw_args = raw_args or {}
    # If SQLite is in-memory (`:memory:`), subprocess cannot share the in-memory connection,
    # so fall back to in-process MCPServer; for file-backed SQLite / PostgreSQL, use real stdio.
    db_str = str(database_engine.url)
    can_use_stdio = use_stdio and ":memory:" not in db_str
    target = (
        _make_stdio_params(database_engine)
        if can_use_stdio
        else build_mcp_server(database_engine)
    )
    return anyio.run(_async_call_tool, target, ctx, tool_name, raw_args)

