export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";

export type Role = "owner" | "owner_rep" | "consultant" | "contractor" | "viewer";

export const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner",
  owner_rep: "Owner's rep",
  consultant: "Consultant",
  contractor: "Contractor",
  viewer: "Viewer",
};

export type Health = "green" | "amber" | "red";

export interface ProjectSummary {
  id: number;
  name: string;
  location: string;
  status: string;
  health: Health;
  actual_pct: number;
  slip_days: number;
  needs_you: number;
  role: Role;
}

export interface Project {
  id: number;
  name: string;
  scope: string;
  location: string;
  owner_name: string;
  currency: string;
  budget: number;
  start_date: string;
  target_end_date: string;
  my_role: Role;
  permissions: string[];
  contractors: { id: number; name: string; trade: string; allowlisted: boolean }[];
}

export interface Me {
  id: number;
  email: string;
  name: string;
  phone: string;
  is_org_admin: boolean;
  projects: { id: number; role: Role; contractor_id: number | null; permissions: string[] }[];
}

export interface Member {
  id: number;
  user_id: number;
  email: string;
  name: string;
  phone: string;
  role: Role;
  contractor_id: number | null;
}

export interface NotificationPrefs {
  email_enabled: boolean;
  whatsapp_enabled: boolean;
  immediate_min_severity: "info" | "warning" | "critical";
  quiet_start_hour: number | null;
  quiet_end_hour: number | null;
  digest_enabled: boolean;
}

export interface OutboxItem {
  id: number;
  project_id: number | null;
  channel: string;
  severity: string;
  title: string;
  status: string;
  error: string;
  created_at: string;
}

export interface Option {
  label: string;
  description: string;
  impact_days?: number | null;
  impact_cost?: number | null;
  risks?: string | null;
}

export interface ChatMessage {
  id: number;
  role: "owner" | "manager" | "system";
  kind: "text" | "alert" | "approval" | "report" | "activity";
  content: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  meta: Record<string, any>;
  created_at: string;
}

export interface MilestoneForecast {
  id: number;
  name: string;
  planned_start: string;
  planned_end: string;
  planned_pct: number;
  actual_pct: number;
  forecast_start: string;
  forecast_end: string;
  slip_days: number;
  is_critical: boolean;
  status: string;
  drivers: string[];
}

export interface RuleHit {
  rule: string;
  rule_key: string;
  severity: "info" | "warning" | "critical";
  title: string;
  details: string;
  refs: string[];
}

export interface ProjectHealth {
  as_of: string;
  budget: number;
  currency: string;
  planned_pct: number;
  actual_pct: number;
  earned_value: number;
  planned_value: number;
  actual_cost: number;
  committed_cost: number;
  spi: number | null;
  cpi: number | null;
  eac: number;
  cost_variance_pct: number;
  target_end: string;
  forecast_end: string;
  project_slip_days: number;
  status: Health;
  milestones: MilestoneForecast[];
  rule_hits: RuleHit[];
}

export interface Alert {
  id: number;
  severity: "info" | "warning" | "critical";
  title: string;
  details: string;
  options: Option[];
  refs: string[];
  status: string;
  created_at: string;
}

export interface ActionEntry {
  id: number;
  actor: string;
  action: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  params: Record<string, any>;
  mode: string;
  status: string;
  reason: string;
  result: string;
  summary: string;
  locked?: boolean;
  created_at: string;
}

export interface WorkItem {
  id: number;
  ref: string;
  worker: string;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  turns_used: number;
  alert_id: number | null;
  result: string;
  updated_at: string;
  created_at: string;
}

export interface Policy {
  action: string;
  description: string;
  mode: "off" | "observe" | "auto" | "approval";
  locked: boolean;
}

export interface Report {
  id: number;
  period: string;
  content: string;
  created_at: string;
}

export class AuthError extends Error {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const res = await fetch(`${API}${path}`, {
    ...init,
    credentials: "include", // session cookie
    headers: { ...(isForm ? {} : { "Content-Type": "application/json" }), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (res.status === 401) {
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
    throw new AuthError("Please sign in");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  projects: () => req<ProjectSummary[]>("/api/projects"),
  project: (id: number) => req<Project>(`/api/projects/${id}`),
  createProject: (body: unknown) => req<{ id: number }>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  health: (id: number) => req<ProjectHealth>(`/api/projects/${id}/health`),
  chat: (id: number) => req<ChatMessage[]>(`/api/projects/${id}/chat`),
  send: (id: number, text: string) =>
    req<{ message: ChatMessage }>(`/api/projects/${id}/chat`, { method: "POST", body: JSON.stringify({ text }) }),
  needsYou: (id: number) => req<{ alerts: Alert[]; approvals: ActionEntry[] }>(`/api/projects/${id}/needs-you`),
  decide: (alertId: number, option_index: number | null, notes = "") =>
    req(`/api/alerts/${alertId}/decide`, { method: "POST", body: JSON.stringify({ option_index, notes }) }),
  decideAction: (actionId: number, approve: boolean, note = "") =>
    req(`/api/actions/${actionId}/decide`, { method: "POST", body: JSON.stringify({ approve, note }) }),
  workItems: (id: number) => req<WorkItem[]>(`/api/projects/${id}/work-items`),
  cancelWork: (wid: number) => req(`/api/work-items/${wid}/cancel`, { method: "POST" }),
  actions: (id: number, status?: string) =>
    req<ActionEntry[]>(`/api/projects/${id}/actions${status ? `?status=${status}` : ""}`),
  policies: (id: number) => req<Policy[]>(`/api/projects/${id}/policies`),
  setPolicy: (id: number, action: string, mode: string) =>
    req(`/api/projects/${id}/policies/${action}`, { method: "PUT", body: JSON.stringify({ mode }) }),
  healthCheck: (id: number) =>
    req<{ status: string; new_hits: number }>(`/api/projects/${id}/health-check`, { method: "POST" }),
  requestReport: (id: number) =>
    req(`/api/projects/${id}/reports`, { method: "POST", body: JSON.stringify({ period: "weekly" }) }),
  reports: (id: number) => req<Report[]>(`/api/projects/${id}/reports`),
  report: (rid: number) => req<Report>(`/api/reports/${rid}`),
  contractors: (id: number) =>
    req<{ id: number; name: string; trade: string; allowlisted: boolean }[]>(`/api/projects/${id}/contractors`),
  dailyUpdate: (id: number, body: { contractor_id?: number; text: string; report_date?: string; file_ids?: number[] }) =>
    req<{ update_id: number; event_id: number | null }>(`/api/projects/${id}/daily-updates`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // auth
  requestCode: (email: string) =>
    req<{ sent: boolean; dev_code?: string }>("/api/auth/request-code", { method: "POST", body: JSON.stringify({ email }) }),
  verify: (email: string, code: string) =>
    req<{ token: string }>("/api/auth/verify", { method: "POST", body: JSON.stringify({ email, code }) }),
  logout: () => req("/api/auth/logout", { method: "POST" }),
  me: () => req<Me>("/api/me"),
  updateMe: (body: { name: string; phone: string }) => req<{ name: string; phone: string }>("/api/me", { method: "PUT", body: JSON.stringify(body) }),
  channelStatus: () =>
    req<{ email: { outbound: boolean; inbound: boolean }; whatsapp: { outbound: boolean; inbound: boolean; template: boolean }; transcription: string }>(
      "/api/channels/status",
    ),
  // team
  members: (id: number) => req<Member[]>(`/api/projects/${id}/members`),
  addMember: (id: number, body: { email: string; name?: string; phone?: string; role: Role; contractor_id?: number | null }) =>
    req<Member>(`/api/projects/${id}/members`, { method: "POST", body: JSON.stringify(body) }),
  removeMember: (id: number, memberId: number) => req(`/api/projects/${id}/members/${memberId}`, { method: "DELETE" }),
  // notifications
  prefs: () => req<NotificationPrefs>("/api/me/notification-prefs"),
  savePrefs: (p: NotificationPrefs) => req<NotificationPrefs>("/api/me/notification-prefs", { method: "PUT", body: JSON.stringify(p) }),
  outbox: () => req<OutboxItem[]>("/api/me/notifications"),
  // schedule
  schedule: (id: number) => req<Schedule>(`/api/projects/${id}/schedule`),
  lookahead: (id: number, weeks = 3) => req<{ has_network: boolean; from?: string; activities: ScheduleActivity[] }>(`/api/projects/${id}/lookahead?weeks=${weeks}`),
  importSchedule: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ activities: number; links: number; milestones: number; baseline_finish: string | null }>(`/api/projects/${id}/schedule/import`, { method: "POST", body: fd });
  },
  // documents, RFIs, submittals
  documents: (id: number) => req<DocItem[]>(`/api/projects/${id}/documents`),
  document: (docId: number) => req<DocItem & { versions: DocVersion[] }>(`/api/documents/${docId}`),
  uploadDocument: (id: number, file: File, meta: Record<string, string> = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(meta)) if (v) fd.append(k, v);
    return req<DocItem & { new_document: boolean; supersedes: string | null }>(`/api/projects/${id}/documents`, { method: "POST", body: fd });
  },
  patchDocument: (docId: number, body: Partial<Pick<DocItem, "number" | "title" | "discipline" | "doc_type" | "status">>) =>
    req<DocItem>(`/api/documents/${docId}`, { method: "PATCH", body: JSON.stringify(body) }),
  searchDocuments: (id: number, q: string) => req<SearchHit[]>(`/api/projects/${id}/documents/search?q=${encodeURIComponent(q)}`),
  rfis: (id: number) => req<Rfi[]>(`/api/projects/${id}/rfis`),
  raiseRfi: (id: number, body: { subject: string; question: string; due_date?: string; activity_id?: number | null }) =>
    req<Rfi>(`/api/projects/${id}/rfis`, { method: "POST", body: JSON.stringify(body) }),
  answerRfi: (rid: number, answer: string, close = false) =>
    req<Rfi>(`/api/rfis/${rid}/answer`, { method: "POST", body: JSON.stringify({ answer, close }) }),
  closeRfi: (rid: number) => req<Rfi>(`/api/rfis/${rid}/close`, { method: "POST" }),
  submittals: (id: number) => req<SubmittalItem[]>(`/api/projects/${id}/submittals`),
  submitSubmittal: (id: number, form: { title: string; spec_section: string; activity_id?: string; file?: File | null }) => {
    const fd = new FormData();
    fd.append("title", form.title);
    fd.append("spec_section", form.spec_section);
    if (form.activity_id) fd.append("activity_id", form.activity_id);
    if (form.file) fd.append("file", form.file);
    return req<SubmittalItem>(`/api/projects/${id}/submittals`, { method: "POST", body: fd });
  },
  reviewSubmittal: (sid: number, status: string, comments: string) =>
    req<SubmittalItem>(`/api/submittals/${sid}/review`, { method: "POST", body: JSON.stringify({ status, comments }) }),
  resubmitSubmittal: (sid: number, file: File, notes: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("notes", notes);
    return req<SubmittalItem>(`/api/submittals/${sid}/resubmit`, { method: "POST", body: fd });
  },
  // money
  commercial: (id: number) => req<Commercial>(`/api/projects/${id}/commercial`),
  submitChangeOrder: (id: number, body: { title: string; description?: string; reason?: string; proposed_amount: number; time_impact_days?: number; contract_id?: number | null; source_ref?: string }) =>
    req<ChangeOrderItem>(`/api/projects/${id}/change-orders`, { method: "POST", body: JSON.stringify(body) }),
  decideChangeOrder: (coId: number, approve: boolean, amount: number | null, note = "") =>
    req<ChangeOrderItem>(`/api/change-orders/${coId}/decide`, { method: "POST", body: JSON.stringify({ approve, amount, note }) }),
  submitPayApp: (id: number, body: { contract_id: number; claims: { contract_line_id: number; claimed_pct: number }[]; notes?: string }) =>
    req<PayAppItem>(`/api/projects/${id}/pay-apps`, { method: "POST", body: JSON.stringify(body) }),
  certifyPayApp: (appId: number, line_pcts: Record<number, number>, note: string) =>
    req<PayAppItem>(`/api/pay-apps/${appId}/certify`, { method: "POST", body: JSON.stringify({ line_pcts, note }) }),
  rejectPayApp: (appId: number, note: string) =>
    req<PayAppItem>(`/api/pay-apps/${appId}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
  // LLM models
  models: () => req<ModelConfigItem[]>("/api/models"),
  discoverModels: () =>
    req<{ ollama: { reachable: boolean; url: string; models: { model: string; size_gb: number; family: string }[] };
          anthropic: { key_present: boolean; models: { model: string; label: string }[] } }>("/api/models/discover"),
  addModel: (body: { label: string; provider: string; model: string; is_default?: boolean }) =>
    req<ModelConfigItem>("/api/models", { method: "POST", body: JSON.stringify(body) }),
  patchModel: (mid: number, body: { label?: string; enabled?: boolean; is_default?: boolean }) =>
    req<ModelConfigItem>(`/api/models/${mid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteModel: (mid: number) => req(`/api/models/${mid}`, { method: "DELETE" }),
  projectModel: (id: number) => req<ProjectModel>(`/api/projects/${id}/model`),
  setProjectModel: (id: number, model_config_id: number | null) =>
    req<ProjectModel>(`/api/projects/${id}/model`, { method: "PUT", body: JSON.stringify({ model_config_id }) }),
  // files
  upload: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ id: number; filename: string; size: number }>(`/api/projects/${id}/files`, { method: "POST", body: fd });
  },
};

export function money(v: number | null | undefined, currency = "INR"): string {
  if (v == null) return "—";
  if (currency === "INR") {
    if (Math.abs(v) >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
    if (Math.abs(v) >= 1e5) return `₹${(v / 1e5).toFixed(1)} L`;
    return `₹${Math.round(v).toLocaleString("en-IN")}`;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(v);
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s.length === 10 ? `${s}T00:00:00` : s);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function timeAgo(s: string): string {
  const t = new Date(s.endsWith("Z") || s.includes("+") ? s : `${s}Z`).getTime();
  const sec = Math.max(0, (Date.now() - t) / 1000);
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export interface ScheduleActivity {
  id: number;
  ref: string;
  name: string;
  milestone_id: number | null;
  duration: number;
  remaining: number;
  status: "done" | "in_progress" | "not_started";
  pct: number;
  early_start: string;
  early_finish: string;
  late_start: string;
  late_finish: string;
  total_float: number;
  free_float: number;
  critical: boolean;
  driver: string;
  baseline_start: string | null;
  baseline_finish: string | null;
  finish_variance_days: number | null;
}

export interface Schedule {
  has_network: boolean;
  data_date: string;
  target_finish: string;
  forecast_finish: string;
  critical_path: number[];
  logic_loops: number[];
  milestones: { id: number; name: string }[];
  activities: ScheduleActivity[];
  dependencies: { pred: number; succ: number; type: string; lag: number }[];
  baselines: { id: number; name: string; source: string; current: boolean; created_at: string }[];
}

export interface DocItem {
  id: number;
  ref: string;
  number: string;
  title: string;
  discipline: string;
  doc_type: string;
  status: string;
  classified: boolean;
  revision: string | null;
  version_id: number | null;
  pages: number;
  searchable: boolean;
  activity_id: number | null;
  updated_at: string;
}

export interface DocVersion {
  id: number;
  revision: string;
  file_id: number;
  source: string;
  notes: string;
  pages: number;
  current: boolean;
  created_at: string;
}

export interface SearchHit {
  cite: string;
  document: string;
  number: string;
  title: string;
  revision: string;
  current: boolean;
  status: string;
  page: number;
  excerpt: string;
}

export interface Rfi {
  id: number;
  number: number;
  subject: string;
  question: string;
  status: "open" | "answered" | "closed" | "void";
  ball_in_court: string;
  due_date: string;
  overdue: boolean;
  answer: string;
  time_impact: boolean;
  cost_impact: boolean;
  activity_id: number | null;
  raised_by: number | null;
  created_at: string;
}

export interface SubmittalItem {
  id: number;
  number: number;
  title: string;
  spec_section: string;
  contractor_id: number | null;
  revision: number;
  status: string;
  due_date: string;
  required_by: string | null;
  overdue: boolean;
  document_id: number | null;
  activity_id: number | null;
  reviewer_comments: string;
}

export function fileUrl(versionId: number): string {
  return `${API}/api/document-versions/${versionId}/file`;
}

export interface ContractItem {
  id: number;
  number: string;
  title: string;
  contractor_id: number | null;
  retention_pct: number;
  value: number;
  lines: { id: number; code: string; description: string; value: number; milestone_id: number | null; activity_id: number | null }[];
}

export interface ChangeOrderItem {
  id: number;
  number: number;
  title: string;
  description: string;
  reason: string;
  stage: "pco" | "submitted" | "approved" | "rejected" | "void";
  contract_id: number | null;
  proposed_amount: number;
  approved_amount: number | null;
  recommended_amount: number | null;
  assessment: string;
  time_impact_days: number;
  source_ref: string;
}

export interface PayAppItem {
  id: number;
  number: number;
  contract_id: number;
  period_end: string;
  status: "submitted" | "certified" | "rejected" | "paid";
  gross_to_date: number;
  certified_to_date: number | null;
  retention: number;
  previous_certified: number;
  amount_due: number;
  notes: string;
  lines: { contract_line_id: number; claimed_pct: number; certified_pct: number | null }[];
}

export interface OverbillItem {
  pay_app_id: number;
  pay_app_number: number;
  contract_id: number;
  line_id: number;
  description: string;
  claimed_pct: number;
  recorded_pct: number;
  excess_value: number;
  ref: string;
}

export interface CashflowPoint {
  month: string;
  planned_cum: number;
  actual_cum: number | null;
  forecast_cum: number;
}

export interface Commercial {
  currency: string;
  summary: null | {
    budget?: number;
    contracts_original?: number;
    approved_changes?: number;
    committed?: number;
    pending_exposure?: number;
    uncommitted?: number;
    forecast_final?: number;
    variance?: number;
    certified_to_date?: number;
    retention_held?: number;
    cashflow?: CashflowPoint[];
    contracts: { id: number; title: string; revised: number; earned: number; claimed_to_date: number; certified_to_date: number; overbilled: number; retention_held: number }[];
    overbilling: OverbillItem[];
  };
  contracts: ContractItem[];
  change_orders: ChangeOrderItem[];
  pay_apps: PayAppItem[];
}

export interface ModelConfigItem {
  id: number;
  label: string;
  provider: "ollama" | "anthropic" | "stub";
  model: string;
  enabled: boolean;
  is_default: boolean;
  available: boolean;
  status: string;
}

export interface ProjectModel {
  model_config_id: number | null;
  provider: string;
  model: string;
  label: string;
  available: boolean;
  status: string;
}
