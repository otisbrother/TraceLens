"""Official MCP Python SDK read-only stdio server for TraceLens.

Never opens a public network port. Every tool call requires a server-signed
`scope_token` and executes inside a DB-enforced read-only transaction using the
shared domain services.
"""

from typing import Any

from mcp.server.mcpserver import MCPServer

from apps.api.contracts import ToolEnvelope
from apps.api.db import engine as default_engine
from apps.api.models import uid
from apps.api.tools import TOOL_SPECIFICATIONS, execute_direct_tool, verify_scope_token


def build_mcp_server(database_engine=default_engine) -> MCPServer:
    mcp_server = MCPServer("tracelens-readonly-mcp")

    def _run_scoped(scope_token: str, tool_name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
        try:
            ctx = verify_scope_token(scope_token)
        except ValueError as exc:
            return ToolEnvelope(
                request_id=uid(),
                source_system="mcp-guard",
                source_record_ids=[],
                as_of="",
                snapshot_id="",
                completeness="unavailable",
                freshness="fixed_snapshot",
                data={},
                error=str(exc),
            ).model_dump()
        return execute_direct_tool(database_engine, ctx, tool_name, raw_args).model_dump()

    @mcp_server.tool(description=TOOL_SPECIFICATIONS["get_case_context"]["description"])
    def get_case_context(
        scope_token: str,
        case_id: str | None = None,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = dict(extra_args or {})
        if case_id is not None:
            args["case_id"] = case_id
        return _run_scoped(scope_token, "get_case_context", args)

    @mcp_server.tool(description=TOOL_SPECIFICATIONS["get_trip_history"]["description"])
    def get_trip_history(
        scope_token: str,
        entity_ids: list[str] | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        cursor: str | None = None,
        page_size: int = 20,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "entity_ids": entity_ids or [],
            "start_at": start_at,
            "end_at": end_at,
            "cursor": cursor,
            "page_size": page_size,
            **(extra_args or {}),
        }
        return _run_scoped(scope_token, "get_trip_history", args)

    @mcp_server.tool(description=TOOL_SPECIFICATIONS["get_device_usage_timeline"]["description"])
    def get_device_usage_timeline(
        scope_token: str,
        entity_ids: list[str] | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "entity_ids": entity_ids or [],
            "start_at": start_at,
            "end_at": end_at,
            **(extra_args or {}),
        }
        return _run_scoped(scope_token, "get_device_usage_timeline", args)

    @mcp_server.tool(description=TOOL_SPECIFICATIONS["get_relationship_subgraph"]["description"])
    def get_relationship_subgraph(
        scope_token: str,
        entity_ids: list[str] | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        max_hops: int = 2,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "entity_ids": entity_ids or [],
            "start_at": start_at,
            "end_at": end_at,
            "max_hops": max_hops,
            **(extra_args or {}),
        }
        return _run_scoped(scope_token, "get_relationship_subgraph", args)

    @mcp_server.tool(description=TOOL_SPECIFICATIONS["get_promo_ledger"]["description"])
    def get_promo_ledger(
        scope_token: str,
        entity_ids: list[str] | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "entity_ids": entity_ids or [],
            "start_at": start_at,
            "end_at": end_at,
            **(extra_args or {}),
        }
        return _run_scoped(scope_token, "get_promo_ledger", args)

    @mcp_server.tool(description=TOOL_SPECIFICATIONS["search_policy"]["description"])
    def search_policy(
        scope_token: str,
        query: str,
        effective_at: str | None = None,
        top_k: int = 4,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "query": query,
            "effective_at": effective_at,
            "top_k": top_k,
            **(extra_args or {}),
        }
        return _run_scoped(scope_token, "search_policy", args)

    @mcp_server.tool(description=TOOL_SPECIFICATIONS["get_source_record"]["description"])
    def get_source_record(
        scope_token: str,
        source_ref: str,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"source_ref": source_ref, **(extra_args or {})}
        return _run_scoped(scope_token, "get_source_record", args)

    return mcp_server


if __name__ == "__main__":
    server = build_mcp_server(default_engine)
    server.run(transport="stdio")

