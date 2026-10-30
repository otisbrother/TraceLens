export type Actor = { id: string; username: string; role: string; tenant_id: string }
export type CaseItem = {
  id: string
  title: string
  status: string
  snapshot_id: string
  cutoff: string
  report_version: number
  disposition: string | null
  alert: {
    rule_ids: string[]
    priority_band: string
    observed_features: { linked_accounts: number; redemption_count: number }
  }
}
export type Row = Record<string, unknown>
export type Claim = { claim_id: string; kind: string; text: string; evidence_ids: string[] }
export type PlannedStep = {
  hypothesis: string
  question: string
  tool_name: string
  arguments: Record<string, unknown>
  rationale: string
  stop_condition: string
}
export type UsageInfo = {
  input_units: number
  output_units: number
  total_units: number
  usage_source: string
  estimated_cost_usd: number | null
  pricing_as_of: string | null
  llm_calls: number
  max_llm_calls: number
  tool_calls: number
  max_tool_calls: number
  repair_attempts: number
  max_repairs: number
}
export type Report = {
  id: string
  version: number
  run_id: string
  content_hash: string
  snapshot_id: string
  payload: {
    summary: string
    hypotheses: string[]
    supporting_claims: Claim[]
    counterevidence_claims: Claim[]
    missing_information: Claim[]
    next_steps: string[]
    planned_steps?: PlannedStep[]
    recommendation: string
    readiness_state?: string
    policy_refs: string[]
    facts: Record<string, number>
    validation_findings: string[]
    workflow_mode?: string
    provider_mode: string
    model_id: string
    retrieval_mode?: string
    transport?: string
    usage?: UsageInfo
  }
}
export type Detail = {
  completeness: string
  snapshot_id: string
  as_of: string
  truncated: boolean
  data: {
    case: CaseItem
    alert: CaseItem['alert']
    facts: Record<string, number>
    records: Record<string, Row[]>
    policies: Row[]
  }
}
export type Run = {
  id: string
  status: string
  error_code: string | null
  workflow_mode?: string
  provider_mode: string
  retrieval_mode?: string
  transport?: string
  parent_run_id?: string | null
  usage_summary?: UsageInfo | null
  created_at: string
}
export type Audit = { id: number; action: string; actor_id: string; created_at: string; payload: Row }
