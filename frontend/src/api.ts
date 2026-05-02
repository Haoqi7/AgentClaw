/**
 * API 层 — 对接 dashboard/server.py
 * 生产环境从同源 (port 7891) 请求，开发环境可通过 VITE_API_URL 指定
 *
 * Problem 4 修复：自动重试、超时保护、连接状态追踪
 */

const API_BASE = import.meta.env.VITE_API_URL || '';

// ── 连接状态追踪 ──
export type ConnectionStatus = 'connected' | 'degraded' | 'disconnected';

let _connStatus: ConnectionStatus = 'connected';
let _consecutiveFailures = 0;
const _connListeners = new Set<(s: ConnectionStatus) => void>();

export function getConnectionStatus(): ConnectionStatus { return _connStatus; }
export function onConnectionStatusChange(fn: (s: ConnectionStatus) => void): () => void {
  _connListeners.add(fn);
  return () => _connListeners.delete(fn);
}

function _setConnStatus(s: ConnectionStatus) {
  if (_connStatus === s) return;
  _connStatus = s;
  _connListeners.forEach(fn => { try { fn(s); } catch {} });
}

function _recordSuccess() {
  _consecutiveFailures = 0;
  _setConnStatus('connected');
}

function _recordFailure() {
  _consecutiveFailures++;
  if (_consecutiveFailures >= 3) _setConnStatus('disconnected');
  else if (_consecutiveFailures >= 1) _setConnStatus('degraded');
}

// ── 重试配置 ──
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 1000;
const FETCH_TIMEOUT_MS = 10_000;

async function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }

/** 判断错误是否为可重试的瞬时错误 */
function _isTransient(err: unknown): boolean {
  if (err instanceof DOMException && err.name === 'AbortError') return false; // 超时不重试
  if (err instanceof TypeError) return true;  // 网络错误 (fetch 本身失败)
  const msg = err instanceof Error ? err.message : String(err);
  // 5xx / 502 / 503 / 504 可重试
  if (/^(500|502|503|504)$/.test(msg)) return true;
  if (/HTTP 50[0-9]/.test(msg)) return true;
  return false;
}

/** 带超时 + 重试的 fetch 包装 */
async function fetchWithRetry(url: string, init?: RequestInit, timeoutMs?: number): Promise<Response> {
  const _timeout = timeoutMs ?? FETCH_TIMEOUT_MS;
  let lastErr: unknown;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), _timeout);
    try {
      const res = await fetch(url, { ...init, signal: controller.signal });
      clearTimeout(timer);
      _recordSuccess();
      return res;
    } catch (err) {
      clearTimeout(timer);
      lastErr = err;
      _recordFailure();
      if (attempt < MAX_RETRIES && _isTransient(err)) {
        await sleep(RETRY_BASE_DELAY_MS * Math.pow(2, attempt));
        continue;
      }
      throw err;
    }
  }
  throw lastErr;
}

// ── 通用请求 ──

async function fetchJ<T>(url: string, timeoutMs?: number): Promise<T> {
  const res = await fetchWithRetry(url, { cache: 'no-store' }, timeoutMs);
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

async function postJ<T>(url: string, data: unknown, timeoutMs?: number): Promise<T> {
  const res = await fetchWithRetry(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }, timeoutMs);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

// ── API 接口 ──

export const api = {
  // 核心数据
  liveStatus: () => fetchJ<LiveStatus>(`${API_BASE}/api/live-status`),
  agentConfig: () => fetchJ<AgentConfig>(`${API_BASE}/api/agent-config`),
  modelChangeLog: () => fetchJ<ChangeLogEntry[]>(`${API_BASE}/api/model-change-log`).catch(() => []),
  officialsStats: () => fetchJ<OfficialsData>(`${API_BASE}/api/officials-stats`),
  morningBrief: (date?: string) =>
    fetchJ<MorningBrief>(date ? `${API_BASE}/api/morning-brief/${date}` : `${API_BASE}/api/morning-brief`),
  morningConfig: () => fetchJ<SubConfig>(`${API_BASE}/api/morning-config`),
  agentsStatus: () => fetchJ<AgentsStatusData>(`${API_BASE}/api/agents-status`),
  pipelineAudit: () => fetchJ<PipelineAuditData>(`${API_BASE}/api/pipeline-audit`),

  // 任务实时动态
  taskActivity: (id: string) =>
    fetchJ<TaskActivityData>(`${API_BASE}/api/task-activity/${encodeURIComponent(id)}`),
  schedulerState: (id: string) =>
    fetchJ<SchedulerStateData>(`${API_BASE}/api/scheduler-state/${encodeURIComponent(id)}`),

  // 技能内容
  skillContent: (agentId: string, skillName: string) =>
    fetchJ<SkillContentResult>(
      `${API_BASE}/api/skill-content/${encodeURIComponent(agentId)}/${encodeURIComponent(skillName)}`
    ),

  // 操作类
  setModel: (agentId: string, model: string) =>
    postJ<ActionResult>(`${API_BASE}/api/set-model`, { agentId, model }),
  setDispatchChannel: (channel: string) =>
    postJ<ActionResult>(`${API_BASE}/api/set-dispatch-channel`, { channel }),
  agentWake: (agentId: string) =>
    postJ<ActionResult>(`${API_BASE}/api/agent-wake`, { agentId }),
  taskAction: (taskId: string, action: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/task-action`, { taskId, action, reason }),
  reviewAction: (taskId: string, action: string, comment: string) =>
    postJ<ActionResult>(`${API_BASE}/api/review-action`, { taskId, action, comment }),
  advanceState: (taskId: string, comment: string) =>
    postJ<ActionResult>(`${API_BASE}/api/advance-state`, { taskId, comment }),
  archiveTask: (taskId: string, archived: boolean) =>
    postJ<ActionResult>(`${API_BASE}/api/archive-task`, { taskId, archived }),
  archiveAllDone: () =>
    postJ<ActionResult & { count?: number }>(`${API_BASE}/api/archive-task`, { archiveAllDone: true }),
  schedulerScan: (thresholdSec = 180) =>
    postJ<ActionResult & { count?: number; actions?: ScanAction[]; checkedAt?: string }>(
      `${API_BASE}/api/scheduler-scan`,
      { thresholdSec }
    ),
  schedulerRetry: (taskId: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/scheduler-retry`, { taskId, reason }),
  schedulerEscalate: (taskId: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/scheduler-escalate`, { taskId, reason }),
  schedulerRollback: (taskId: string, reason: string) =>
    postJ<ActionResult>(`${API_BASE}/api/scheduler-rollback`, { taskId, reason }),
  refreshMorning: () =>
    postJ<ActionResult>(`${API_BASE}/api/morning-brief/refresh`, {}),
  morningBriefHistory: () =>
    fetchJ<{ok: boolean; dates: string[]}>(`${API_BASE}/api/morning-brief-history`),
  saveMorningConfig: (config: SubConfig) =>
    postJ<ActionResult>(`${API_BASE}/api/morning-config`, config),
  notificationChannels: () =>
    fetchJ<{ok: boolean; channels: ChannelInfo[]}>(`${API_BASE}/api/notification-channels`),
  checkFeeds: (urls: string[]) =>
    postJ<{ok: boolean; results: FeedCheckResult[]}>(`${API_BASE}/api/morning-brief/check-feeds`, {urls}),
  addSkill: (agentId: string, skillName: string, description: string, trigger: string) =>
    postJ<ActionResult>(`${API_BASE}/api/add-skill`, { agentId, skillName, description, trigger }),

  // 远程 Skills 管理
  addRemoteSkill: (agentId: string, skillName: string, sourceUrl: string, description?: string) =>
    postJ<ActionResult & { skillName?: string; agentId?: string; source?: string; localPath?: string; size?: number; addedAt?: string }>(
      `${API_BASE}/api/add-remote-skill`, { agentId, skillName, sourceUrl, description: description || '' }
    ),
  remoteSkillsList: () =>
    fetchJ<RemoteSkillsListResult>(`${API_BASE}/api/remote-skills-list`),
  updateRemoteSkill: (agentId: string, skillName: string) =>
    postJ<ActionResult>(`${API_BASE}/api/update-remote-skill`, { agentId, skillName }),
  removeRemoteSkill: (agentId: string, skillName: string) =>
    postJ<ActionResult>(`${API_BASE}/api/remove-remote-skill`, { agentId, skillName }),

  // ClawHub 技能商店
  clawhubSearch: (query: string, limit?: number) =>
    fetchJ<{ok: boolean; results: ClawHubSkill[]; query: string; total: number; error?: string}>(
      `${API_BASE}/api/clawhub/search?q=${encodeURIComponent(query)}&limit=${limit || 20}`
    ),
  clawhubInstall: (agentId: string, slug: string) =>
    postJ<ActionResult>(`${API_BASE}/api/clawhub/install`, { agentId, slug }),
  clawhubInfo: () =>
    fetchJ<{ok: boolean; base: string; reachable: boolean}>(`${API_BASE}/api/clawhub/info`),
  clawhubPreview: (slug: string) =>
    fetchJ<{ok: boolean; slug?: string; content?: string; error?: string}>(`${API_BASE}/api/clawhub/preview?slug=${encodeURIComponent(slug)}`),
  githubSkillPreview: (url: string) =>
    fetchJ<{ok: boolean; content?: string; error?: string}>(`${API_BASE}/api/github-skill-preview?url=${encodeURIComponent(url)}`),

  // ── 订阅任务管理 ──
  morningTasks: () =>
    fetchJ<{ok: boolean; tasks: SubscriptionTask[]}>(`${API_BASE}/api/morning-tasks`),
  createMorningTask: (task: Omit<SubscriptionTask, 'id' | 'createdAt' | 'updatedAt'>) =>
    postJ<ActionResult & { task?: SubscriptionTask }>(`${API_BASE}/api/morning-tasks`, task),
  updateMorningTask: (id: string, updates: Partial<SubscriptionTask>) =>
    postJ<ActionResult>(`${API_BASE}/api/morning-tasks/${encodeURIComponent(id)}`, { ...updates, _method: 'PUT' }),
  deleteMorningTask: (id: string) =>
    postJ<ActionResult>(`${API_BASE}/api/morning-tasks/${encodeURIComponent(id)}`, { _method: 'DELETE' }),
  collectTask: (id: string) =>
    postJ<ActionResult>(`${API_BASE}/api/morning-tasks/${encodeURIComponent(id)}/collect`, {}),
  pushTest: (id: string) =>
    postJ<ActionResult>(`${API_BASE}/api/morning-tasks/${encodeURIComponent(id)}/push-test`, {}),
  pushHistory: (id: string) =>
    fetchJ<{ok: boolean; history: PushHistoryItem[]}>(`${API_BASE}/api/morning-tasks/${encodeURIComponent(id)}/push-history`),

  // ── 任务专属简报 API（数据隔离） ──
  morningBriefTask: (taskId: string) =>
    fetchJ<MorningBrief>(`${API_BASE}/api/morning-brief/task/${encodeURIComponent(taskId)}`),
  morningBriefTaskDate: (taskId: string, date: string) =>
    fetchJ<MorningBrief>(`${API_BASE}/api/morning-brief/task/${encodeURIComponent(taskId)}/${date}`),
  morningBriefTaskHistory: (taskId: string) =>
    fetchJ<{ok: boolean; dates: string[]}>(`${API_BASE}/api/morning-brief/task/${encodeURIComponent(taskId)}/history`),

  createTask: (data: CreateTaskPayload) =>
    postJ<ActionResult & { taskId?: string }>(`${API_BASE}/api/create-task`, data),

  // ── 朝堂议政 ──
  courtDiscussStart: (topic: string, officials: string[], taskId?: string) =>
    postJ<CourtDiscussSessionData>(`${API_BASE}/api/court-discuss/start`, { topic, officials, taskId }),
  // 朝堂议政推进：后端需调 LLM 生成多位官员讨论，耗时 5~30s，使用 90s 超时
  courtDiscussAdvance: (sessionId: string, userMessage?: string, decree?: string) =>
    postJ<CourtDiscussResult>(`${API_BASE}/api/court-discuss/advance`, { sessionId, userMessage, decree }, 90_000),
  courtDiscussConclude: (sessionId: string) =>
    postJ<ActionResult & { summary?: string }>(`${API_BASE}/api/court-discuss/conclude`, { sessionId }, 90_000),
  courtDiscussDestroy: (sessionId: string) =>
    postJ<ActionResult>(`${API_BASE}/api/court-discuss/destroy`, { sessionId }),
  courtDiscussFate: () =>
    fetchJ<{ ok: boolean; event: string }>(`${API_BASE}/api/court-discuss/fate`),
  courtDiscussList: () =>
    fetchJ<{ ok: boolean; sessions: CourtSessionSummary[] }>(
      `${API_BASE}/api/court-discuss/list`
    ),
  courtDiscussSession: (sessionId: string) =>
    fetchJ<CourtDiscussSessionData>(`${API_BASE}/api/court-discuss/session/${encodeURIComponent(sessionId)}`),

  // ── 监察排除 ──
  auditExclude: (taskId: string, action: 'exclude' | 'include' = 'exclude') =>
    postJ<ActionResult & { excluded_count?: number }>(`${API_BASE}/api/audit-exclude`, { taskId, action }),

  // ── 任务删除 ──
  deleteTask: (taskId: string, confirmId?: string) =>
    postJ<ActionResult>(`${API_BASE}/api/delete-task`, { taskId, confirmId: confirmId || '' }),

  // ── Gateway 会话管理（代理） ──
  gatewayConversations: () =>
    fetchJ<GatewayConversationsResult>(`${API_BASE}/api/gateway/conversations`),
  gatewayDeleteConversation: (conversationId: string) =>
    postJ<ActionResult>(`${API_BASE}/api/gateway/conversation/${encodeURIComponent(conversationId)}/delete`, {}),
  gatewayClearAgentSessions: (agentId: string) =>
    postJ<ActionResult & { cleared?: number }>(`${API_BASE}/api/gateway/clear-agent-sessions`, { agentId }),
  gatewaySessionsUrl: () =>
    fetchJ<{ ok: boolean; url: string }>(`${API_BASE}/api/gateway/sessions-url`),

  // ═══════════════════════════════════════════════════════════
  // [TaskOutput] 新增：产出管理 API
  // ═══════════════════════════════════════════════════════════
  taskOutputList: (taskId: string) =>
    fetchJ<TaskOutputListResult>(`${API_BASE}/api/outputs/${encodeURIComponent(taskId)}`),
  taskOutputPreview: (taskId: string, filename: string) =>
    fetchJ<TaskOutputPreviewResult>(`${API_BASE}/api/outputs/${encodeURIComponent(taskId)}/preview/${encodeURIComponent(filename)}`),
  taskOutputDelete: (taskId: string, filename: string) =>
    postJ<ActionResult>(`${API_BASE}/api/outputs/${encodeURIComponent(taskId)}/delete`, { filename }),
};

// ── Types ──

export interface ActionResult {
  ok: boolean;
  message?: string;
  error?: string;
}

export interface FlowEntry {
  at: string;
  from: string;
  to: string;
  remark: string;
}

export interface TodoItem {
  id: string | number;
  title: string;
  status: 'not-started' | 'in-progress' | 'completed';
  detail?: string;
}

export interface Heartbeat {
  status: 'active' | 'warn' | 'stalled' | 'unknown' | 'idle';
  label: string;
}

export interface Task {
  id: string;
  title: string;
  state: string;
  org: string;
  now: string;
  eta: string;
  block: string;
  ac: string;
  output: string;
  heartbeat: Heartbeat;
  flow_log: FlowEntry[];
  todos: TodoItem[];
  review_round: number;
  archived: boolean;
  archivedAt?: string;
  updatedAt?: string;
  sourceMeta?: Record<string, unknown>;
  activity?: ActivityEntry[];
  _prev_state?: string;
}

export interface SyncStatus {
  ok: boolean;
  [key: string]: unknown;
}

export interface LiveStatus {
  tasks: Task[];
  syncStatus: SyncStatus;
}

export interface AgentInfo {
  id: string;
  label: string;
  emoji: string;
  role: string;
  model: string;
  skills: SkillInfo[];
}

export interface SkillInfo {
  name: string;
  description: string;
  path: string;
}

export interface KnownModel {
  id: string;
  label: string;
  provider: string;
}

export interface AgentConfig {
  agents: AgentInfo[];
  knownModels?: KnownModel[];
  dispatchChannel?: string;
}

export interface ChangeLogEntry {
  at: string;
  agentId: string;
  oldModel: string;
  newModel: string;
  rolledBack?: boolean;
}

export interface OfficialInfo {
  id: string;
  label: string;
  emoji: string;
  role: string;
  rank: string;
  model: string;
  model_short: string;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  cost_cny: number;
  cost_usd: number;
  sessions: number;
  messages: number;
  tasks_done: number;
  tasks_active: number;
  flow_participations: number;
  merit_score: number;
  merit_rank: number;
  last_active: string;
  heartbeat: Heartbeat;
  participated_edicts: { id: string; title: string; state: string }[];
}

export interface OfficialsData {
  officials: OfficialInfo[];
  totals: { tasks_done: number; cost_cny: number };
  top_official: string;
}

export interface AgentStatusInfo {
  id: string;
  label: string;
  emoji: string;
  role: string;
  status: 'running' | 'idle' | 'offline' | 'unconfigured';
  statusLabel: string;
  lastActive?: string;
}

export interface GatewayStatus {
  alive: boolean;
  probe: boolean;
  status: string;
}

export interface AgentsStatusData {
  ok: boolean;
  gateway: GatewayStatus;
  agents: AgentStatusInfo[];
  checkedAt: string;
}

export interface MorningNewsItem {
  title: string;
  summary?: string;
  desc?: string;
  link: string;
  source: string;
  image?: string;
  pub_date?: string;
}

export interface MorningBrief {
  date?: string;
  generated_at?: string;
  categories: Record<string, MorningNewsItem[]>;
}

export interface SubCategoryConfig {
  name: string;
  enabled: boolean;
}

export interface CustomFeed {
  name: string;
  url: string;
  category: string;
}

export interface FeedSource {
  name: string;
  url: string;
  category: string;
  protected?: boolean;  // 兼容旧数据，新数据不再使用
}

export interface NotificationConfig {
  enabled: boolean;
  channel: string;
  webhook: string;
}

/** 订阅任务卡片 */
export interface SubscriptionTask {
  id: string;
  name: string;
  emoji: string;
  categories: string[];
  feedUrls: string[];
  keywords: string[];       // 任务级关键词过滤（兼容旧数据，新任务置空）
  categoryKeywords?: Record<string, string[]>;  // 分类维度关键词：{"科技": ["AI", "芯片"], "经济": ["GDP"]}
  maxItems?: number;          // 每个分类最多采集条数，默认5
  notification: NotificationConfig;
  createdAt: string;
  updatedAt: string;
}

/** 推送历史条目 */
export interface PushHistoryItem {
  taskId: string;
  channel: string;
  status: 'success' | 'failed';
  itemCount: number;
  pushedAt: string;
  error?: string;
}

export interface SubConfig {
  categories: SubCategoryConfig[];
  keywords: string[];
  feeds: FeedSource[];
  custom_feeds?: CustomFeed[];      // 兼容旧数据
  notification: NotificationConfig;
  feishu_webhook?: string;          // 兼容旧数据
  tasks?: SubscriptionTask[];       // 订阅任务卡片（最多12个）
}

export interface ChannelInfo {
  id: string;
  label: string;
  icon: string;
  placeholder: string;
}

export interface FeedCheckResult {
  url: string;
  status: 'ok' | 'error';
  title?: string;
  itemCount?: number;
  latency_ms?: number;
  error?: string;
}

export interface ClawHubSkill {
  slug: string;
  name: string;
  description: string;
  downloads?: number;
  stars?: number;
  owner?: { handle?: string };
  version?: string;
}

export interface ActivityEntry {
  kind: string;
  at?: number | string;
  text?: string;
  thinking?: string;
  agent?: string;
  from?: string;
  to?: string;
  remark?: string;
  tools?: { name: string; input_preview?: string }[];
  tool?: string;
  output?: string;
  exitCode?: number | null;
  items?: TodoItem[];
  diff?: {
    changed?: { id: string; from: string; to: string }[];
    added?: { id: string; title: string }[];
    removed?: { id: string; title: string }[];
  };
}

export interface PhaseDuration {
  phase: string;
  durationSec: number;
  durationText: string;
  ongoing?: boolean;
}

export interface TodosSummary {
  total: number;
  completed: number;
  inProgress: number;
  notStarted: number;
  percent: number;
}

export interface ResourceSummary {
  totalTokens?: number;
  totalCost?: number;
  totalElapsedSec?: number;
}

export interface TaskActivityData {
  ok: boolean;
  message?: string;
  error?: string;
  activity?: ActivityEntry[];
  relatedAgents?: string[];
  agentLabel?: string;
  lastActive?: string;
  phaseDurations?: PhaseDuration[];
  totalDuration?: string;
  todosSummary?: TodosSummary;
  resourceSummary?: ResourceSummary;
}

export interface SchedulerInfo {
  retryCount?: number;
  escalationLevel?: number;
  lastDispatchStatus?: string;
  stallThresholdSec?: number;
  enabled?: boolean;
  lastProgressAt?: string;
  lastDispatchAt?: string;
  lastDispatchAgent?: string;
  autoRollback?: boolean;
}

export interface SchedulerStateData {
  ok: boolean;
  error?: string;
  scheduler?: SchedulerInfo;
  stalledSec?: number;
}

export interface SkillContentResult {
  ok: boolean;
  name?: string;
  agent?: string;
  content?: string;
  path?: string;
  error?: string;
}

export interface ScanAction {
  taskId: string;
  action: string;
  to?: string;
  toState?: string;
  stalledSec?: number;
}

export interface CreateTaskPayload {
  title: string;
  org: string;
  targetDept?: string;
  priority?: string;
  templateId?: string;
  params?: Record<string, string>;
}

export interface RemoteSkillItem {
  skillName: string;
  agentId: string;
  sourceUrl: string;
  description: string;
  localPath: string;
  addedAt: string;
  lastUpdated: string;
  status: 'valid' | 'not-found' | string;
}

export interface RemoteSkillsListResult {
  ok: boolean;
  remoteSkills?: RemoteSkillItem[];
  count?: number;
  listedAt?: string;
  error?: string;
}

// ── 朝堂议政 ──

export interface CourtDiscussResult {
  ok: boolean;
  session_id?: string;
  topic?: string;
  round?: number;
  new_messages?: Array<{
    official_id: string;
    name: string;
    content: string;
    emotion?: string;
    action?: string;
  }>;
  scene_note?: string;
  total_messages?: number;
  error?: string;
}

export interface CourtDiscussSessionData {
  ok: boolean;
  error?: string;
  session_id: string;
  topic: string;
  task_id?: string;
  officials: Array<{
    id: string;
    name: string;
    emoji: string;
    role: string;
    personality: string;
    speaking_style: string;
  }>;
  messages: Array<{
    type: string;
    content: string;
    official_id?: string;
    official_name?: string;
    emotion?: string;
    action?: string;
    timestamp?: number;
  }>;
  round: number;
  phase: string;
}

export interface CourtSessionSummary {
  session_id: string;
  topic: string;
  round: number;
  phase: string;
  official_count: number;
  message_count: number;
}

// ── 流程监察 ──

export interface AuditViolation {
  task_id: string;
  title: string;
  type: '越权调用' | '流程跳步' | '断链超时' | '直接执行越权' | '极端停滞' | '未完成回奏' | '会话未注册' | '会话通信过多' | '会话可疑' | '会话违规';
  detail: string;
  flow_index?: number;
  detected_at: string;
}

export interface WatchedTask {
  task_id: string;
  title: string;
  state: string;
  org: string;
  flow_count: number;
  session_keys?: Record<string, {
    sessionKey: string;
    savedAt: string;
    agents: string[];
  }>;
  session_key_count?: number;
}

export interface AuditNotification {
  type: '越权通报' | '跳步通报' | '断链唤醒' | '断链通知' | '会话警告' | '归档' | '巡检'
    | '唤醒' | '通知' | '违规';  // 兼容旧格式
  to: string;
  task_id?: string;
  task_ids?: string[];
  summary: string;
  sent_at: string;
  status: 'sent' | 'failed';
  detail: string;
}

export interface PipelineAuditData {
  last_check: string;
  violations: AuditViolation[];
  watched_tasks: WatchedTask[];
  watched_count: number;
  check_count: number;
  total_violations: number;
  notifications: AuditNotification[];
  archived_violations?: AuditViolation[];
  archived_notifications?: AuditNotification[];
}

// ── Gateway 会话管理 ──

export interface GatewayConversation {
  id: string;
  agent_id?: string;
  agentId?: string;
  title?: string;
  created_at?: string;
  createdAt?: string;
  updated_at?: string;
  updatedAt?: string;
  message_count?: number;
  messageCount?: number;
  status?: string;
  [key: string]: unknown;
}

export interface GatewayConversationsResult {
  ok: boolean;
  conversations?: GatewayConversation[];
  total?: number;
  error?: string;
}

// ═══════════════════════════════════════════════════════════
// [TaskOutput] 新增：产出管理 Types
// ═══════════════════════════════════════════════════════════

export interface TaskArtifact {
  name: string;
  dept: string;
  type: string;
  size: number;
  path: string;
  uploadedAt: string;
}

export interface TaskOutputListResult {
  ok: boolean;
  taskId?: string;
  taskTitle?: string;
  artifacts?: TaskArtifact[];
  totalSize?: number;
  error?: string;
}

export interface TaskOutputPreviewResult {
  ok: boolean;
  content?: string;
  filename?: string;
  size?: number;
  error?: string;
}
