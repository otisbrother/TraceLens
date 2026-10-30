"""Bounded single-agent LangGraph investigation workflow with persistent checkpointer.

State machine nodes:
1. load_and_verify_scope
2. gather_core_facts
3. formulate_hypotheses
4. select_next_step
5. execute_targeted_tool
6. lookup_policy
7. draft_report
8. validate_report_node
9. repair_report_node
10. publish_awaiting_review
11. human_review_interrupt (LangGraph `interrupt()`)
12. finalize_disposition (resumed via `Command(resume=...)` after verified human review)
"""

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.contracts import (
    NextStepDecision,
    TrustedContext,
    UsageProvenance,
)
from apps.api.domain import digest, gather_facts, readonly_session, require_case
from apps.api.models import InvestigationRun, Job, Report, User, uid
from apps.api.providers import LiveLLMProvider, MockProvider
from apps.api.reports import build_report, repair_report, validate_report
from apps.api.services import audit
from apps.api.tools import execute_direct_tool
from apps.mcp.client import execute_mcp_tool
from apps.worker.observations import envelope_from_observations


class WorkflowState(TypedDict, total=False):
    run_id: str
    job_id: str
    lease_token: str
    case_id: str
    tenant_id: str
    actor_id: str
    snapshot_id: str
    cutoff: str
    workflow_mode: str
    provider_mode: str
    model_id: str
    retrieval_mode: str
    transport: str
    started_monotonic: float
    completed_tools: list[str]
    planned_steps: list[dict[str, Any]]
    hypotheses: list[str]
    facts_preview: dict[str, Any]
    observations: dict[str, dict[str, Any]]
    usage: dict[str, Any]
    pending_step: dict[str, Any] | None
    stop_planning: bool
    report_version: int
    report_hash: str
    validation_findings: list[str]
    repaired: bool
    published: bool
    review_decision: dict[str, Any] | None
    finalized: bool


def checkpoint_db_path(database_engine) -> str:
    db_name = database_engine.url.database
    if not db_name or db_name == ":memory:":
        p = Path(".cache") / "inmem_checkpoints.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    if settings.checkpoint_path:
        p = Path(settings.checkpoint_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    p = Path(f"{db_name}.checkpoints.sqlite")
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


@contextmanager
def open_checkpointer(database_engine):
    path = checkpoint_db_path(database_engine)
    with SqliteSaver.from_conn_string(path) as saver:
        yield saver


def _dispatch_tool(
    database_engine,
    ctx: TrustedContext,
    transport: str,
    tool_name: str,
    arguments: dict[str, Any],
):
    if transport == "mcp":
        return execute_mcp_tool(database_engine, ctx, tool_name, arguments, use_stdio=True)
    return execute_direct_tool(database_engine, ctx, tool_name, arguments)


def build_investigation_graph(database_engine):
    def load_and_verify_scope(state: WorkflowState) -> WorkflowState:
        with readonly_session(database_engine) as reader:
            run = reader.get(InvestigationRun, state["run_id"])
            if not run:
                raise ValueError("RUN_NOT_FOUND")
            actor = reader.get(User, run.actor_id)
            if not actor or not actor.active or actor.role != "investigator":
                raise ValueError("ACTOR_REVOKED")
            case = require_case(reader, actor, run.case_id)
            if case.snapshot_id != run.snapshot_id:
                raise ValueError("SNAPSHOT_CHANGED")
            return {
                "case_id": case.id,
                "tenant_id": case.tenant_id,
                "actor_id": actor.id,
                "snapshot_id": case.snapshot_id,
                "cutoff": case.cutoff,
                "workflow_mode": run.workflow_mode or "C",
                "transport": run.transport or settings.tool_transport,
                "retrieval_mode": run.retrieval_mode or settings.retrieval_mode,
                "started_monotonic": time.monotonic(),
                "completed_tools": [],
                "planned_steps": [],
                "hypotheses": [],
                "facts_preview": {},
                "observations": {},
                "usage": UsageProvenance(
                    max_tool_calls=settings.max_tool_calls,
                    max_llm_calls=settings.max_llm_calls,
                    max_repairs=settings.max_repairs,
                ).model_dump(),
                "pending_step": None,
                "stop_planning": False,
                "validation_findings": [],
                "repaired": False,
                "published": False,
                "review_decision": None,
                "finalized": False,
            }

    def gather_core_facts(state: WorkflowState) -> WorkflowState:
        ctx = TrustedContext(
            actor_id=state["actor_id"],
            username=state["actor_id"],
            role="investigator",
            tenant_id=state["tenant_id"],
            case_id=state["case_id"],
            snapshot_id=state["snapshot_id"],
            cutoff=state["cutoff"],
            run_id=state["run_id"],
        )
        usage = UsageProvenance.model_validate(state["usage"])
        completed = list(state.get("completed_tools", []))
        preview: dict[str, Any] = dict(state.get("facts_preview", {}))
        observations = dict(state.get("observations", {}))

        # Both Mode B and Mode C start with deterministic case context & promo ledger
        for tool_name, args in [
            ("get_case_context", {"case_id": state["case_id"]}),
            ("get_promo_ledger", {}),
        ]:
            if usage.tool_calls >= usage.max_tool_calls:
                raise ValueError("TOOL_BUDGET_EXCEEDED")
            env = _dispatch_tool(database_engine, ctx, state["transport"], tool_name, args)
            usage.tool_calls += 1
            completed.append(tool_name)
            observations[tool_name] = env.model_dump()
            preview[tool_name] = {
                "completeness": env.completeness,
                "error": env.error,
                "source_count": len(env.source_record_ids),
                "data": env.data,
            }
            if tool_name == "get_promo_ledger" and env.data:
                preview["net_vnd"] = env.data.get("net_vnd", 0)
                preview["reversal_vnd"] = env.data.get("reversal_vnd", 0)

        return {
            "completed_tools": completed,
            "facts_preview": preview,
            "observations": observations,
            "usage": usage.model_dump(),
        }

    def formulate_hypotheses(state: WorkflowState) -> WorkflowState:
        hypotheses = [
            "H1 (Đáng nghi): Nhóm tài khoản mới phối hợp dùng chung thiết bị đồng thời để trục lợi voucher với 1 tài xế.",
            "H2 (Hợp lệ): Thiết bị dùng chung trong hộ gia đình hoặc chuyển nhượng kế tiếp (không chồng lấn thời gian).",
            "H3 (Hợp lệ): Trùng mạng IP văn phòng, đi làm định kỳ hoặc các giao dịch khuyến mãi đã có bút toán đảo (reversal).",
        ]
        # In Mode B (fixed checklist), we do not dynamically branch by hypothesis;
        # we schedule fixed trip_history + search_policy and stop adaptive planning.
        if state["workflow_mode"] == "B":
            fixed_step = NextStepDecision(
                hypothesis="Checklist cố định Mode B",
                question="Thu thập danh sách chuyến đi cơ bản theo checklist cố định.",
                tool_name="get_trip_history",
                arguments={"page_size": 10},
                rationale="Thực thi bước cố định trong Mode B, không chọn tool linh hoạt theo giả thuyết.",
                stop_condition="Hoàn tất bước cố định Mode B.",
            )
            return {
                "hypotheses": hypotheses,
                "pending_step": fixed_step.model_dump(),
                "planned_steps": [fixed_step.model_dump()],
                "stop_planning": True,
            }
        return {"hypotheses": hypotheses}

    def select_next_step(state: WorkflowState) -> WorkflowState:
        if (
            time.monotonic() - state.get("started_monotonic", time.monotonic())
            > settings.wall_clock_timeout_seconds
        ):
            raise ValueError("WALL_CLOCK_TIMEOUT")
        usage = UsageProvenance.model_validate(state["usage"])
        completed = list(state.get("completed_tools", []))
        if usage.tool_calls >= usage.max_tool_calls:
            return {"pending_step": None, "stop_planning": True}

        if state["provider_mode"] == "live":
            # Reserve one provider call for the report.
            if usage.llm_calls >= max(0, usage.max_llm_calls - 1) and usage.max_llm_calls > 0:
                return {"pending_step": None, "stop_planning": True}
            provider = LiveLLMProvider()
            decision = provider.plan_next_step(completed, state["facts_preview"], usage)
        else:
            provider = MockProvider()
            decision = provider.plan_next_step(completed, state["facts_preview"])

        planned = list(state.get("planned_steps", []))
        planned.append(decision.model_dump())
        if decision.tool_name == "STOP" or decision.tool_name in completed:
            return {
                "pending_step": None,
                "planned_steps": planned,
                "stop_planning": True,
                "usage": usage.model_dump(),
            }
        return {
            "pending_step": decision.model_dump(),
            "planned_steps": planned,
            "stop_planning": False,
            "usage": usage.model_dump(),
        }

    def execute_targeted_tool(state: WorkflowState) -> WorkflowState:
        step_raw = state.get("pending_step")
        if not step_raw:
            return {"stop_planning": True}
        step = NextStepDecision.model_validate(step_raw)
        if step.tool_name == "STOP":
            return {"pending_step": None, "stop_planning": True}

        usage = UsageProvenance.model_validate(state["usage"])
        if usage.tool_calls >= usage.max_tool_calls:
            return {"pending_step": None, "stop_planning": True}

        ctx = TrustedContext(
            actor_id=state["actor_id"],
            username=state["actor_id"],
            role="investigator",
            tenant_id=state["tenant_id"],
            case_id=state["case_id"],
            snapshot_id=state["snapshot_id"],
            cutoff=state["cutoff"],
            run_id=state["run_id"],
        )
        env = _dispatch_tool(database_engine, ctx, state["transport"], step.tool_name, step.arguments)
        usage.tool_calls += 1
        completed = list(state.get("completed_tools", []))
        completed.append(step.tool_name)
        preview = dict(state.get("facts_preview", {}))
        preview[step.tool_name] = {
            "completeness": env.completeness,
            "error": env.error,
            "source_count": len(env.source_record_ids),
            "data": env.data,
        }
        observations = dict(state.get("observations", {}))
        observations[step.tool_name] = env.model_dump()
        return {
            "completed_tools": completed,
            "facts_preview": preview,
            "observations": observations,
            "pending_step": None,
            "usage": usage.model_dump(),
        }

    def lookup_policy(state: WorkflowState) -> WorkflowState:
        completed = list(state.get("completed_tools", []))
        observations = dict(state.get("observations", {}))
        usage = UsageProvenance.model_validate(state["usage"])
        if "search_policy" not in completed and usage.tool_calls < usage.max_tool_calls:
            ctx = TrustedContext(
                actor_id=state["actor_id"],
                username=state["actor_id"],
                role="investigator",
                tenant_id=state["tenant_id"],
                case_id=state["case_id"],
                snapshot_id=state["snapshot_id"],
                cutoff=state["cutoff"],
                run_id=state["run_id"],
            )
            env = _dispatch_tool(
                database_engine,
                ctx,
                state["transport"],
                "search_policy",
                {"query": "thiết bị voucher reversal nguồn duyệt", "top_k": 3},
            )
            usage.tool_calls += 1
            completed.append("search_policy")
            observations["search_policy"] = env.model_dump()
        return {"completed_tools": completed, "usage": usage.model_dump(), "observations": observations}

    def draft_validate_and_publish(state: WorkflowState) -> WorkflowState:
        """Executes deterministic report synthesis, validation, bounded 1-step repair if needed,
        and idempotent persistence of Evidence, Report, and AuditEvents before human review interrupt."""
        # Idempotency guard: if this node re-executes during LangGraph resume, do not duplicate DB writes!
        with Session(database_engine) as db:
            existing_report = db.scalar(select(Report).where(Report.run_id == state["run_id"]))
            if existing_report:
                return {
                    "published": True,
                    "report_version": existing_report.version,
                    "report_hash": existing_report.content_hash,
                }

        if time.monotonic() - state["started_monotonic"] > settings.wall_clock_timeout_seconds:
            raise ValueError("WALL_CLOCK_TIMEOUT")
        usage = UsageProvenance.model_validate(state["usage"])
        planned_models = [NextStepDecision.model_validate(s) for s in state.get("planned_steps", [])]

        with readonly_session(database_engine) as reader:
            run = reader.get(InvestigationRun, state["run_id"])
            actor = reader.get(User, run.actor_id)
            if not actor or not actor.active or actor.role != "investigator":
                raise ValueError("ACTOR_REVOKED")
            case = require_case(reader, actor, run.case_id)
            if case.snapshot_id != run.snapshot_id:
                raise ValueError("SNAPSHOT_CHANGED")

            # Do not bypass STOP, tool errors, budgets or the chosen transport with a final full read.
            envelope = envelope_from_observations(state)
            content, evidence = build_report(
                case,
                run,
                envelope,
                workflow_mode=state["workflow_mode"],
                provider_mode=state["provider_mode"],
                model_id=state["model_id"],
                retrieval_mode=state["retrieval_mode"],
                transport=state["transport"],
                planned_steps=planned_models,
                usage=usage,
            )

            if state["provider_mode"] == "live":
                live_provider = LiveLLMProvider()
                llm_out = live_provider.call_structured_json(
                    system_prompt=(
                        "Provide a concise Vietnamese investigation summary in JSON key 'summary' "
                        "based strictly on the provided structured facts. Do not invent numbers."
                    ),
                    user_payload={"facts": envelope.data["facts"], "recommendation": content.recommendation},
                    usage=usage,
                )
                if not isinstance(llm_out.get("summary"), str) or not llm_out["summary"].strip():
                    raise ValueError("PROVIDER_INVALID_SCHEMA")
                content.summary = llm_out["summary"].strip()
                content.usage = usage

            # Recompute for verification; never add unseen facts to the report.
            all_facts = gather_facts(reader, actor, case.id, include_policies=False).data["facts"]
            expected_facts = {key: all_facts[key] for key in envelope.data["facts"] if key in all_facts}
            findings = validate_report(reader, actor, case, run, content, evidence, expected_facts)
            if findings and usage.repair_attempts < settings.max_repairs:
                content, findings = repair_report(
                    reader,
                    actor,
                    case,
                    run,
                    content,
                    evidence,
                    expected_facts,
                    findings,
                )
            if findings:
                raise ValueError("VALIDATION_FAILED")

        with Session(database_engine) as db:
            # Fencing: an expired worker cannot publish after another worker has reclaimed its job.
            changed = db.execute(
                update(Job)
                .where(
                    Job.id == state["job_id"],
                    Job.lease_token == state["lease_token"],
                    Job.status == "running",
                    Job.lease_until >= int(time.time()),
                )
                .values(status="done", lease_token=None, lease_until=0)
            ).rowcount
            if not changed:
                db.rollback()
                return {"published": False}

            run = db.get(InvestigationRun, state["run_id"])
            actor = db.get(User, run.actor_id)
            case = require_case(db, actor, run.case_id)
            if not actor.active or actor.role != "investigator" or case.snapshot_id != run.snapshot_id:
                raise ValueError("ACTOR_OR_SNAPSHOT_CHANGED")

            case.report_version += 1
            case.status = "awaiting_review"
            db.add_all(evidence)
            payload = content.model_dump()
            report = Report(
                id=uid(),
                case_id=case.id,
                run_id=run.id,
                tenant_id=case.tenant_id,
                snapshot_id=case.snapshot_id,
                version=case.report_version,
                content_hash=digest(payload),
                payload=payload,
            )
            db.add(report)
            run.status = "awaiting_review"
            run.checkpoint_thread_id = run.id
            run.usage_summary = usage.model_dump()

            audit(
                db,
                actor,
                "tool.completed",
                case.id,
                run.id,
                {
                    "tool": "aggregate_observations",
                    "completed_tools": state.get("completed_tools", []),
                    "transport": state["transport"],
                    "workflow_mode": state["workflow_mode"],
                    "snapshot_id": case.snapshot_id,
                    "completeness": envelope.completeness,
                    "source_count": len(envelope.source_record_ids),
                    "tool_calls": usage.tool_calls,
                    "max_tool_calls": usage.max_tool_calls,
                },
            )
            audit(
                db,
                actor,
                "report.validated",
                case.id,
                run.id,
                {
                    "version": report.version,
                    "report_hash": report.content_hash,
                    "validation": "scope_snapshot_sources_typed_facts",
                    "semantic_support": "human_review_required",
                    "workflow_mode": state["workflow_mode"],
                    "provider_mode": state["provider_mode"],
                    "llm_calls": usage.llm_calls,
                    "retrieval_mode": state["retrieval_mode"],
                    "transport": state["transport"],
                },
            )
            db.commit()
            return {
                "published": True,
                "report_version": report.version,
                "report_hash": report.content_hash,
                "usage": usage.model_dump(),
            }

    def human_review_interrupt(state: WorkflowState) -> WorkflowState:
        if not state.get("published"):
            return {"finalized": True}
        decision = interrupt(
            {
                "run_id": state["run_id"],
                "case_id": state["case_id"],
                "report_version": state.get("report_version"),
                "report_hash": state.get("report_hash"),
                "snapshot_id": state["snapshot_id"],
            }
        )
        return {"review_decision": decision, "finalized": True}

    def finalize_disposition(state: WorkflowState) -> WorkflowState:
        return {"finalized": True}

    def route_after_hypotheses(state: WorkflowState) -> str:
        if state["workflow_mode"] == "B":
            return "execute_targeted_tool"
        return "select_next_step"

    def route_after_select(state: WorkflowState) -> str:
        if state.get("stop_planning") or not state.get("pending_step"):
            return "lookup_policy"
        return "execute_targeted_tool"

    def route_after_execute(state: WorkflowState) -> str:
        if state.get("stop_planning") or state["workflow_mode"] == "B":
            return "lookup_policy"
        return "select_next_step"

    graph = StateGraph(WorkflowState)
    graph.add_node("load_and_verify_scope", load_and_verify_scope)
    graph.add_node("gather_core_facts", gather_core_facts)
    graph.add_node("formulate_hypotheses", formulate_hypotheses)
    graph.add_node("select_next_step", select_next_step)
    graph.add_node("execute_targeted_tool", execute_targeted_tool)
    graph.add_node("lookup_policy", lookup_policy)
    graph.add_node("draft_validate_and_publish", draft_validate_and_publish)
    graph.add_node("human_review_interrupt", human_review_interrupt)
    graph.add_node("finalize_disposition", finalize_disposition)

    graph.add_edge(START, "load_and_verify_scope")
    graph.add_edge("load_and_verify_scope", "gather_core_facts")
    graph.add_edge("gather_core_facts", "formulate_hypotheses")
    graph.add_conditional_edges(
        "formulate_hypotheses",
        route_after_hypotheses,
        {"execute_targeted_tool": "execute_targeted_tool", "select_next_step": "select_next_step"},
    )
    graph.add_conditional_edges(
        "select_next_step",
        route_after_select,
        {"execute_targeted_tool": "execute_targeted_tool", "lookup_policy": "lookup_policy"},
    )
    graph.add_conditional_edges(
        "execute_targeted_tool",
        route_after_execute,
        {"select_next_step": "select_next_step", "lookup_policy": "lookup_policy"},
    )
    graph.add_edge("lookup_policy", "draft_validate_and_publish")
    graph.add_edge("draft_validate_and_publish", "human_review_interrupt")
    graph.add_edge("human_review_interrupt", "finalize_disposition")
    graph.add_edge("finalize_disposition", END)
    return graph


def run_workflow_until_review(
    database_engine,
    run_id: str,
    job_id: str,
    lease_token: str,
    provider_mode: str = "mock",
    model_id: str = "deterministic-template-v1",
) -> dict[str, Any]:
    config = {"configurable": {"thread_id": run_id}}
    initial_state: WorkflowState = {
        "run_id": run_id,
        "job_id": job_id,
        "lease_token": lease_token,
        "provider_mode": provider_mode,
        "model_id": model_id,
    }
    with open_checkpointer(database_engine) as saver:
        compiled = build_investigation_graph(database_engine).compile(checkpointer=saver)
        return compiled.invoke(initial_state, config)


def resume_workflow_after_review(
    database_engine,
    run_id: str,
    decision_payload: dict[str, Any],
) -> dict[str, Any] | None:
    config = {"configurable": {"thread_id": run_id}}
    with open_checkpointer(database_engine) as saver:
        compiled = build_investigation_graph(database_engine).compile(checkpointer=saver)
        snapshot = compiled.get_state(config)
        if not snapshot or not snapshot.values:
            raise ValueError("CHECKPOINT_MISSING")
        if not snapshot.next and snapshot.values.get("review_decision") == decision_payload:
            return snapshot.values
        if "human_review_interrupt" not in snapshot.next:
            raise ValueError("CHECKPOINT_NOT_AWAITING_REVIEW")
        return compiled.invoke(Command(resume=decision_payload), config)


def get_workflow_checkpoint_state(database_engine, run_id: str) -> dict[str, Any]:
    config = {"configurable": {"thread_id": run_id}}
    with open_checkpointer(database_engine) as saver:
        compiled = build_investigation_graph(database_engine).compile(checkpointer=saver)
        snapshot = compiled.get_state(config)
        if not snapshot or not snapshot.values:
            return {"exists": False, "next": [], "values": {}}
        return {
            "exists": True,
            "next": list(snapshot.next),
            "values": dict(snapshot.values),
        }
