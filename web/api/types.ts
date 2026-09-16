/**
 * The console contract (UI spec §52).
 *
 * These mirror `rewyn.ui.schemas` exactly. They are checked against the
 * server's OpenAPI document by `tests/contract.test.ts`, so a field that is
 * renamed in Python fails here rather than rendering as `undefined`.
 */

export type Surface = "local" | "cloud";
export type EntryStatus = "ok" | "error" | "warning" | "running";
export type ToolStatus = "success" | "error" | "denied";
export type HealthStatus = "healthy" | "degraded" | "unhealthy" | "unknown";

export interface ProblemDetail {
  error: string;
  detail: string;
  action: string | null;
  href: string | null;
}

export interface ProjectLocation {
  surface: Surface;
  project: string;
  home: string | null;
  endpoint: string | null;
  how_to_change: string;
}

export interface ConsoleCounts {
  runs: number;
  agents: number;
  datasets: number;
  reports: number;
  failures: number;
  versions: number;
}

export interface OnboardingStep {
  key: string;
  title: string;
  detail: string;
  done: boolean;
  code: string;
  href: string | null;
  action: string;
}

export interface UseCase {
  key: string;
  title: string;
  question: string;
  detail: string;
  href: string;
  ready: boolean;
  needs: string;
}

export interface Onboarding {
  location: ProjectLocation;
  counts: ConsoleCounts;
  empty: boolean;
  demo_loaded: boolean;
  can_load_demo: boolean;
  summary: string;
  steps: OnboardingStep[];
  jobs: UseCase[];
}

export interface Capabilities {
  surface: Surface;
  api_version: string;
  sdk_version: string;
  project: string;
  project_id: string | null;
  role: string;
  features: string[];
  environments: string[];
  location: ProjectLocation | null;
  counts: ConsoleCounts;
  demo_loaded: boolean;
}

export interface RunSummary {
  id: string;
  name: string;
  status: string;
  project: string;
  environment: string;
  agent: string | null;
  user: string | null;
  model: string | null;
  session_id: string | null;
  parent_run_id: string | null;
  started_at: string;
  ended_at: string | null;
  duration_ms: number;
  cost: number;
  input_tokens: number;
  output_tokens: number;
  model_calls: number;
  tool_calls: number;
  event_count: number;
  error: string | null;
  tags: string[];
  eval_score: number | null;
}

export interface RunPage {
  runs: RunSummary[];
  total: number;
  estimated: boolean;
  next_cursor: string | null;
}

export interface DependencyView {
  kind: string;
  name: string;
  version: string;
  fingerprint: string | null;
  metadata: Record<string, unknown>;
}

export interface PanelCounts {
  timeline: number;
  graph: number;
  context: number;
  model: number;
  prompt: number;
  memory: number;
  tools: number;
  mcp: number;
  skills: number;
  guardrails: number;
  approvals: number;
}

export interface CostBreakdown {
  model: number;
  tool: number;
  embedding: number;
  retrieval: number;
  sandbox: number;
  total: number;
  currency: string;
}

export interface RunDetail {
  run: RunSummary;
  input: unknown;
  output: unknown;
  error: string | null;
  traceback: string | null;
  dependencies: DependencyView[];
  panels: PanelCounts;
  cost: CostBreakdown;
  metadata: Record<string, unknown>;
}

export interface TimelineEntry {
  seq: number;
  type: string;
  timestamp: string;
  offset_ms: number;
  span_id: string | null;
  parent_span_id: string | null;
  depth: number;
  label: string;
  detail: string;
  status: EntryStatus;
  duration_ms: number | null;
  payload: Record<string, unknown>;
}

export interface TimelineView {
  run_id: string;
  started_at: string;
  entries: TimelineEntry[];
  total: number;
  next_seq: number | null;
}

export interface EventPage {
  run_id: string;
  events: Record<string, unknown>[];
  total: number;
  next_seq: number | null;
}

export interface GraphNodeView {
  id: string;
  label: string;
  kind: string;
  status: "ok" | "error" | "running" | "skipped";
  started_at: string | null;
  duration_ms: number | null;
  seq: number | null;
  parent: string | null;
  detail: Record<string, unknown>;
}

export interface GraphEdgeView {
  source: string;
  target: string;
  kind: "flow" | "spawn" | "handoff";
  label: string;
}

export interface GraphView {
  run_id: string;
  shape: "graph" | "agents" | "linear";
  root: string | null;
  nodes: GraphNodeView[];
  edges: GraphEdgeView[];
}

export interface ContextItemView {
  id: string;
  kind: string;
  title: string | null;
  source: string | null;
  record: string | null;
  version: string | null;
  uri: string | null;
  retrieved_at: string | null;
  hash: string | null;
  tokens: number;
  relevance: number;
  authority: number;
  trust_level: string;
  sensitivity: string;
  verified: boolean | null;
  included: boolean;
  excluded_reason: string | null;
  redacted: boolean;
  redaction_reason: string | null;
}

export interface ContextAssembly {
  seq: number;
  context: string;
  version: string;
  query: string | null;
  fingerprint: string | null;
  budget: number;
  used: number;
  by_kind: Record<string, number>;
  items: ContextItemView[];
  dropped_untrusted: string[];
  stale: string[];
  assembled_in_ms: number;
}

export interface ContextView {
  run_id: string;
  assemblies: ContextAssembly[];
  retrievals: Record<string, unknown>[];
}

export interface ModelCallView {
  index: number;
  seq: number;
  provider: string;
  model: string;
  temperature: number | null;
  max_tokens: number | null;
  top_p: number | null;
  seed: number | null;
  stop: string[];
  reasoning: Record<string, unknown>;
  output_schema: Record<string, unknown> | null;
  tools: string[];
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cost: number;
  cost_source: string;
  latency_ms: number;
  finish_reason: string | null;
  request_fingerprint: string | null;
  error: string | null;
  text: string;
}

export interface ModelView {
  run_id: string;
  calls: ModelCallView[];
  providers: string[];
  models: string[];
  total_cost: number;
  total_latency_ms: number;
}

export interface PromptMessageView {
  role: string;
  name: string | null;
  text: string;
  hash: string;
  tokens: number;
}

export interface PromptCallView {
  index: number;
  seq: number;
  model: string;
  fingerprint: string | null;
  system: PromptMessageView[];
  developer: PromptMessageView[];
  user: PromptMessageView[];
  assistant: PromptMessageView[];
  tool: PromptMessageView[];
  tool_instructions: PromptMessageView[];
  skill_instructions: PromptMessageView[];
}

export interface PromptView {
  run_id: string;
  calls: PromptCallView[];
}

export interface MemoryOpView {
  seq: number;
  operation: "read" | "write";
  memory: string;
  kind: string;
  id: string | null;
  content: string;
  query: string | null;
  score: number | null;
  confidence: number | null;
  importance: number | null;
  provider: string | null;
  created_at: string | null;
  timestamp: string;
}

export interface MemoryView {
  run_id: string;
  operations: MemoryOpView[];
}

export interface ToolCallView {
  index: number;
  seq: number;
  tool_call_id: string;
  name: string;
  version: string | null;
  fingerprint: string | null;
  server: string | null;
  arguments: Record<string, unknown>;
  result: unknown;
  status: ToolStatus;
  duration_ms: number;
  replayable: boolean;
}

export interface ToolView {
  run_id: string;
  calls: ToolCallView[];
}

export interface McpToolView {
  name: string;
  fingerprint: string | null;
  risk_level: string | null;
  calls: number;
}

export interface McpServerView {
  server: string;
  version: string;
  transport: string | null;
  fingerprint: string | null;
  connected_at: string | null;
  latency_ms: number;
  tools: McpToolView[];
  resources: string[];
  changed_since_previous_run: boolean;
  previous_version: string | null;
}

export interface McpView {
  run_id: string;
  servers: McpServerView[];
}

export interface SkillUseView {
  skill: string;
  version: string;
  fingerprint: string | null;
  state: "discovered" | "loaded" | "used";
  description: string;
  resources: string[];
  scripts: string[];
  allowed_tools: string[];
  activations: number;
}

export interface SkillsView {
  run_id: string;
  skills: SkillUseView[];
}

export interface GuardrailView {
  seq: number;
  guardrail: string;
  stage: string;
  triggered: boolean;
  action: string | null;
  detail: Record<string, unknown>;
}

export interface ApprovalView {
  request_id: string;
  action: string;
  risk: string | null;
  details: Record<string, unknown>;
  requested_at: string;
  decided_at: string | null;
  decision: "pending" | "approved" | "rejected";
  by: string | null;
  reason: string | null;
  run_id: string | null;
}

export interface ScoreView {
  subject_run_id: string;
  evaluator: string;
  version: string;
  value: number;
  passed: boolean;
  label: string | null;
  reason: string;
  threshold: number | null;
  scored_at: string;
  scored_in_run_id: string;
}

/* Replay and compare (UI §20-§25) */

export type Setting = "original" | "new" | "recorded" | "live";

export const REPLAY_COMPONENTS = [
  "model",
  "prompt",
  "context",
  "memory",
  "skills",
  "tools",
  "mcp",
  "temperature",
  "system",
] as const;

export type ReplayComponentName = (typeof REPLAY_COMPONENTS)[number];

export interface ReplayComponent {
  component: string;
  mode: Setting;
  value: string | null;
}

export interface ReplayRequest {
  components: ReplayComponent[];
}

export interface ReplayComponentPlan {
  component: string;
  mode: Setting;
  value: string | null;
  supported: boolean;
  effect: string;
}

export interface ReplayPlan {
  run_id: string;
  mode: "reconstruct" | "prompt" | "execute";
  components: ReplayComponentPlan[];
  notes: string[];
}

export interface PromptComparison {
  index: number;
  original_model: string;
  replay_model: string;
  original_text: string;
  replay_text: string;
  original_tool_calls: string[];
  replay_tool_calls: string[];
  original_cost: number;
  replay_cost: number;
  original_latency_ms: number;
  replay_latency_ms: number;
  changed: boolean;
  error: string | null;
}

export interface MismatchView {
  kind: string;
  reason: string;
  index: number;
  expected: string | null;
  actual: string | null;
  detail: string;
}

export interface ReplayView {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  plan: ReplayPlan;
  original_run_id: string;
  replay_run_id: string | null;
  started_at: string;
  finished_at: string | null;
  identical: boolean | null;
  faithful: boolean | null;
  original_output: unknown;
  output: unknown;
  prompts: PromptComparison[];
  mismatches: MismatchView[];
  substitutions: number;
  original_cost: number;
  cost: number;
  duration_ms: number;
  problem: ProblemDetail | null;
}

export interface DifferenceView {
  dimension: string;
  field: string;
  kind: string;
  before: unknown;
  after: unknown;
  delta: number | null;
  description: string;
}

export interface ExplanationView {
  observed: string;
  cause: string | null;
  confidence: number;
  rationale: string;
  description: string;
}

export interface DimensionSummary {
  dimension: string;
  changed: boolean;
  differences: number;
}

export interface DiffView {
  run_a: string;
  run_b: string;
  summary_a: RunSummary;
  summary_b: RunSummary;
  identical: boolean;
  dimensions: DimensionSummary[];
  differences: DifferenceView[];
  explanations: ExplanationView[];
  output_changed: boolean;
  cost_delta_percent: number | null;
  latency_delta_percent: number | null;
  quality_delta: number | null;
}

export interface DatasetCaseView {
  id: string;
  input: unknown;
  expected: unknown;
  tags: string[];
  source_run_id: string | null;
  evaluator: string | null;
  severity: string | null;
  created_at: string;
  last_result: string | null;
  status: string | null;
}

export interface DatasetSummary {
  name: string;
  version: string;
  description: string;
  cases: number;
  tags: string[];
  updated_at: string;
  passed: number | null;
  failed: number | null;
  latest_run: string | null;
}

export interface DatasetDetail extends DatasetSummary {
  items: DatasetCaseView[];
}

export interface SaveAsTestRequest {
  dataset: string;
  expected: unknown;
  evaluator: string | null;
  severity: "critical" | "high" | "medium" | "low" | null;
  tags: string[];
}

/* BUILD registries, agents and quality (UI §26-§30) */

export interface RegistryVersion {
  version: string;
  runs: number;
  first_seen: string | null;
  last_seen: string | null;
  fingerprint: string | null;
}

export interface RegistryEntry {
  kind: string;
  name: string;
  version: string;
  versions: number;
  runs: number;
  last_used_at: string | null;
}

export interface RegistryDetail {
  kind: string;
  name: string;
  runs: number;
  versions: RegistryVersion[];
  used_by: string[];
  recent_runs: RunSummary[];
  metadata: Record<string, unknown>;
}

export interface AgentSummary {
  name: string;
  kind: string;
  version: string;
  versions: number;
  environments: string[];
  runs: number;
  success_rate: number;
  avg_cost: number;
  avg_latency_ms: number;
  eval_score: number | null;
  model: string | null;
  tools: number;
  skills: string[];
  mcp_servers: string[];
  memory: boolean;
  last_run_at: string | null;
}

export interface AgentDetail extends AgentSummary {
  version_history: RegistryVersion[];
  dependencies: DependencyView[];
  recent_runs: RunSummary[];
  reports: RegressionSummary[];
}

export interface DriftFindingView {
  kind: string;
  dependency_kind: string;
  name: string;
  before: string | null;
  after: string | null;
  before_version: string | null;
  after_version: string | null;
  likely_cause: string;
}

export interface ManifestDiff {
  application: string;
  before_version: string;
  after_version: string;
  findings: DriftFindingView[];
  unchanged: number;
}

export interface ScoreBucket {
  lower: number;
  upper: number;
  count: number;
}

export interface EvaluatorSummary {
  name: string;
  scores: number;
  mean: number;
  pass_rate: number;
  distribution: ScoreBucket[];
  last_scored_at: string | null;
}

export interface EvaluationsView {
  evaluators: EvaluatorSummary[];
  scores: number;
  runs_scored: number;
}

export interface MetricSummaryView {
  name: string;
  passed: number;
  total: number;
  mean: number;
  pass_rate: number;
  threshold: number | null;
  baseline_mean: number | null;
}

export interface GateView {
  name: string;
  passed: boolean;
  actual: number;
  limit: number;
  comparison: string;
  detail: string;
}

export interface RegressionSummary {
  id: string;
  dataset: string;
  dataset_version: string;
  target: string;
  created_at: string;
  tests: number;
  succeeded: number;
  success_rate: number;
  success_delta: number | null;
  avg_cost: number;
  avg_latency_ms: number;
  passed: boolean;
  baseline_id: string | null;
}

export interface RegressionCaseView {
  item_id: string;
  run_id: string | null;
  passed: boolean;
  output: string;
  expected: unknown;
  cost: number;
  latency_ms: number;
  error: string | null;
  scores: Record<string, number>;
  regressed: boolean;
}

export interface RegressionDetail extends RegressionSummary {
  metrics: MetricSummaryView[];
  gates: GateView[];
  cases: RegressionCaseView[];
  regressions: RegressionCaseView[];
  baseline: RegressionSummary | null;
}

export interface ExperimentRequest {
  dataset: string;
  target: string;
  baseline: string | null;
  evaluators: string[];
  min_success: number | null;
  max_cost: number | null;
  concurrency: number;
}

export interface ExperimentPlan {
  dataset: string;
  command: string;
  explanation: string;
  reports_url: string;
}

/* Live and approvals (UI §34, §35, §53) */

export interface LiveRun {
  run: RunSummary;
  events: number;
  stage: string;
  context_tokens: number;
  cost: number;
  nodes: GraphNodeView[];
  updated_at: string;
}

export interface LiveFrame {
  runs: LiveRun[];
  sent_at: string;
}

export interface StreamFrame {
  run_id: string;
  entries: TimelineEntry[];
  status: string;
  finished: boolean;
  sent_at: string;
}

export interface ApprovalDecisionRequest {
  approved: boolean;
  by: string;
  reason: string;
  correction: Record<string, unknown> | null;
}

export interface PendingApprovalView {
  request_id: string;
  action: string;
  risk: string;
  details: Record<string, unknown>;
  requested_at: string;
  run_id: string | null;
  decision: "pending" | "approved" | "rejected";
  by: string | null;
  reason: string | null;
  expires_at: string | null;
  expired: boolean;
}

/* Intelligence: dependencies, drift, cost, releases (UI §28, §31-§33, §40, §43) */

export interface DependencyNode {
  id: string;
  kind: string;
  name: string;
  version: string;
  runs: number;
  href: string | null;
}

export interface DependencyEdge {
  source: string;
  target: string;
  relation: string;
}

export interface DependencyMap {
  root: string;
  agent: string;
  runs: number;
  nodes: DependencyNode[];
  edges: DependencyEdge[];
  fingerprint: string;
}

export interface DriftCause {
  kind: string;
  dependency_kind: string;
  name: string;
  before: string | null;
  after: string | null;
  detail: string;
  changed: boolean;
}

export interface DriftView {
  agent: string;
  baseline_runs: number;
  current_runs: number;
  baseline_from: string | null;
  current_from: string | null;
  expected_success: number;
  current_success: number;
  expected_cost: number;
  current_cost: number;
  drifted: boolean;
  silent: boolean;
  causes: DriftCause[];
  unchanged: string[];
}

export interface CostSlice {
  key: string;
  total: number;
  runs: number;
  succeeded: number;
  per_run: number;
  per_successful_task: number | null;
  share: number;
}

export interface CostView {
  group_by: string;
  total: number;
  runs: number;
  succeeded: number;
  per_successful_task: number | null;
  categories: CostBreakdown;
  slices: CostSlice[];
  currency: string;
}

export interface ReleaseComponent {
  kind: string;
  name: string;
  before: string | null;
  after: string | null;
}

export interface PromotionRecord {
  release_id: string;
  application: string;
  version: string;
  environment: string;
  by: string;
  at: string;
  report_id: string | null;
  note: string;
}

export interface PromoteRequest {
  environment?: string;
  note?: string;
  by?: string | null;
}

export interface ReleaseView {
  id: string;
  application: string;
  version: string;
  previous_version: string | null;
  created_at: string | null;
  runs: number;
  changed: ReleaseComponent[];
  tests: number;
  passed: number;
  failed: number;
  success_rate: number | null;
  cost_delta_percent: number | null;
  report_id: string | null;
  status: "ready" | "blocked" | "unverified";
  blocked_by: string[];
  promotion: string;
  promoted: PromotionRecord | null;
}

export interface VariantView {
  label: string;
  report_id: string;
  target: string;
  tests: number;
  success_rate: number;
  avg_cost: number;
  avg_latency_ms: number;
  failures: number;
  metrics: Record<string, number>;
  winner: boolean;
}

export interface ExperimentView {
  dataset: string;
  variants: VariantView[];
  measured: string[];
}

export interface NotificationView {
  id: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  summary: string;
  detail: string;
  at: string;
  href: string | null;
}

export interface OverviewMetric {
  label: string;
  value: number;
  unit: "percent" | "seconds" | "currency" | "count";
  delta: number | null;
}

export interface RecentChange {
  at: string;
  kind: string;
  name: string;
  before: string | null;
  after: string | null;
  run_id: string;
  summary: string;
}

export interface Incident {
  id: string;
  severity: "warning" | "critical";
  summary: string;
  detail: string;
  run_ids: string[];
  started_at: string | null;
  affected_runs: number;
  likely_cause: string | null;
  agent: string | null;
  environment: string | null;
}

export interface Overview {
  project: string;
  environment: string | null;
  status: HealthStatus;
  metrics: OverviewMetric[];
  recent_changes: RecentChange[];
  incidents: Incident[];
  recent_runs: RunSummary[];
}

export interface SearchHit {
  group: string;
  id: string;
  label: string;
  detail: string;
  href: string;
}

export interface SearchResults {
  query: string;
  groups: string[];
  hits: SearchHit[];
  total: number;
}

export interface SessionView {
  id: string;
  runs: number;
  started_at: string;
  ended_at: string | null;
  agents: string[];
  user: string | null;
  cost: number;
  failures: number;
}

export interface EnvironmentView {
  name: string;
  runs: number;
  last_run_at: string | null;
}

export interface FacetValue {
  value: string;
  count: number;
}

export interface RunFacets {
  agent: FacetValue[];
  model: FacetValue[];
  user: FacetValue[];
  status: FacetValue[];
  environment: FacetValue[];
  tool: FacetValue[];
  mcp: FacetValue[];
  skill: FacetValue[];
  tag: FacetValue[];
  error: FacetValue[];
}

// The control room: incidents, collaboration, narrative, workspace (UI §23, §36, §37, §60)
export type IncidentStatus = "open" | "investigating" | "mitigated" | "resolved";

export interface IncidentStage {
  stage: string;
  reached: boolean;
  at: string | null;
  summary: string;
  evidence: string;
  href: string | null;
}

export interface IncidentComment {
  id: string;
  subject: string;
  author: string;
  body: string;
  created_at: string;
  resolved: boolean;
}

export interface IncidentDetail extends Incident {
  status: IncidentStatus;
  assignee: string | null;
  first_seen: string | null;
  last_seen: string | null;
  cause_evidence: string[];
  timeline: IncidentStage[];
  runs: RunSummary[];
  comments: IncidentComment[];
  note: string;
}

export interface IncidentUpdate {
  status?: IncidentStatus | null;
  assignee?: string | null;
  note?: string | null;
}

export interface CommentRequest {
  subject: string;
  body: string;
  author?: string | null;
}

export interface SavedView {
  id: string;
  name: string;
  screen: string;
  query: string;
  author: string;
  created_at: string;
  shared: boolean;
  description: string;
}

export interface SavedViewRequest {
  name: string;
  screen: string;
  query?: string;
  description?: string;
  shared?: boolean;
  author?: string | null;
}

export interface NarrativeClaim {
  label: "Observed" | "Inference";
  text: string;
  evidence: string[];
  confidence: number | null;
}

export interface NarrativeView {
  run_a: string;
  run_b: string;
  author: "model" | "rules";
  model: string | null;
  run_id: string | null;
  summary: string;
  claims: NarrativeClaim[];
  grounded: boolean;
  dropped: string[];
  note: string;
}

export interface WorkspaceStage {
  key: string;
  label: string;
  question: string;
  ready: boolean;
  href: string;
  summary: string;
  detail: string;
  count: number | null;
  status: "ok" | "warn" | "fail" | "idle";
}

export interface WorkspaceComponent {
  kind: string;
  name: string;
  version: string;
  href: string | null;
}

export interface WorkspaceView {
  agent: string;
  version: string;
  environment: string | null;
  components: WorkspaceComponent[];
  stages: WorkspaceStage[];
  loop: WorkspaceStage[];
  latest_run: RunSummary | null;
  recent_runs: RunSummary[];
  incidents: Incident[];
  next_step: string;
}
