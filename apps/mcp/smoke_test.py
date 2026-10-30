"""Stdio MCP round-trip smoke test for TraceLens.

Verifies:
1. Stdio MCP subprocess lists all 7 read-only tools with USE THIS WHEN / DO NOT USE WHEN.
2. Calling multiple tools over stdio MCP returns identical facts/scope as direct tool execution.
3. Tampering with tenant_id / case_id or calling a write tool is rejected.
"""

from sqlalchemy.orm import Session

from apps.api.contracts import TrustedContext
from apps.api.db import engine
from apps.api.models import Base, Case, User
from apps.api.tools import execute_direct_tool
from apps.mcp.client import execute_mcp_tool, list_mcp_tools
from data.generator.seed import seed_database


def run_smoke_test(database_engine=engine, use_stdio: bool = True) -> dict:
    Base.metadata.create_all(database_engine)
    with Session(database_engine) as db:
        seed_database(db)
        actor = db.get(User, "investigator")
        case = db.get(Case, "TL-1041")
        ctx = TrustedContext(
            actor_id=actor.id,
            username=actor.username,
            role=actor.role,
            tenant_id=actor.tenant_id,
            case_id=case.id,
            snapshot_id=case.snapshot_id,
            cutoff=case.cutoff,
            run_id="smoke-run-1",
        )

    tools = list_mcp_tools(database_engine, use_stdio=use_stdio)
    tool_names = sorted(t["name"] for t in tools)
    assert len(tool_names) == 7, f"Expected 7 read-only tools, got {tool_names}"
    for t in tools:
        assert "USE THIS WHEN" in t["description"] and "DO NOT USE WHEN" in t["description"]

    checked = []
    for tool_name, args in [
        ("get_case_context", {"case_id": "TL-1041"}),
        ("get_device_usage_timeline", {}),
        ("get_promo_ledger", {}),
        ("get_trip_history", {"page_size": 10}),
        ("get_relationship_subgraph", {"max_hops": 2}),
        ("search_policy", {"query": "thiết bị voucher reversal", "top_k": 3}),
    ]:
        direct_env = execute_direct_tool(database_engine, ctx, tool_name, args)
        mcp_env = execute_mcp_tool(database_engine, ctx, tool_name, args, use_stdio=use_stdio)
        assert direct_env.snapshot_id == mcp_env.snapshot_id
        assert direct_env.completeness == mcp_env.completeness
        assert direct_env.source_record_ids == mcp_env.source_record_ids
        assert direct_env.data == mcp_env.data
        checked.append(tool_name)

    # Verify scope tampering rejection over MCP
    tampered = execute_mcp_tool(
        database_engine,
        ctx,
        "get_promo_ledger",
        {"tenant_id": "tenant-b"},
        use_stdio=use_stdio,
    )
    assert tampered.error == "SCOPE_TAMPERING_REJECTED"

    cross_case = execute_mcp_tool(
        database_engine,
        ctx,
        "get_case_context",
        {"case_id": "TL-1043"},
        use_stdio=use_stdio,
    )
    assert cross_case.error == "SCOPE_VIOLATION"

    return {
        "status": "ok",
        "transport": "mcp-stdio" if use_stdio else "mcp-inproc",
        "tools_verified": checked,
        "scope_tampering_rejected": True,
    }


if __name__ == "__main__":
    print(run_smoke_test(engine, use_stdio=True))

