"""Report inputs consist of actual scoped tool responses, never a hidden final gather."""

from apps.api.contracts import ToolEnvelope
from apps.api.models import uid

FACT_KEYS = {
    "get_case_context": ("account_count", "new_accounts"),
    "get_promo_ledger": ("redemption_vnd", "reversal_vnd", "net_vnd"),
    "get_trip_history": ("trip_count", "driver_count", "trip_span_minutes"),
    "get_device_usage_timeline": ("overlap_pairs",),
}


def envelope_from_observations(state):
    observations = {
        name: ToolEnvelope.model_validate(raw) for name, raw in state.get("observations", {}).items()
    }
    required = {"get_case_context", "get_promo_ledger", "get_trip_history", "search_policy"}
    if state["workflow_mode"] == "C":
        required.add("get_device_usage_timeline")
    facts, refs, policies, problems = {}, set(), [], []
    for name, env in observations.items():
        if env.snapshot_id != state["snapshot_id"]:
            raise ValueError("SNAPSHOT_CHANGED")
        refs.update(env.source_record_ids)
        if env.completeness != "complete" or env.error or env.truncated:
            problems.append(f"{name}:{env.error or 'INCOMPLETE_OR_TRUNCATED'}")
        if env.completeness == "unavailable":
            continue
        for key in FACT_KEYS.get(name, ()):
            if key in env.data:
                facts[key] = env.data[key]
        if name == "search_policy":
            policies = env.data.get("policies", [])
    problems.extend(f"{name}:NOT_COLLECTED" for name in sorted(required - observations.keys()))
    if not policies:
        problems.append("search_policy:NO_APPLICABLE_POLICY")
    return ToolEnvelope(
        request_id=uid(),
        source_system="collected-tool-observations",
        source_record_ids=sorted(refs),
        as_of=state["cutoff"],
        snapshot_id=state["snapshot_id"],
        completeness="partial" if problems else "complete",
        freshness="fixed_snapshot",
        truncated=any(env.truncated for env in observations.values()),
        error=";".join(problems) if problems else None,
        data={"facts": facts, "policies": policies},
    )
