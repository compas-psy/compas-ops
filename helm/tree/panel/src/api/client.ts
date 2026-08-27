/* Клиент Panel API.
 *
 * Два свойства, ради которых он существует отдельно от компонентов:
 *
 * 1. Каждый блок грузится сам и падает сам. Бриф §2: «ошибка загрузки блока —
 *    показывается внутри блока, остальной экран работает». Общий try/catch на
 *    экран это свойство ломает.
 * 2. Запись всегда идёт через step-up. Функция записи физически требует
 *    идентификатор свежей passkey-церемонии — вызвать её без него нельзя,
 *    это не договорённость, а сигнатура.
 */

export type Health = 'ok' | 'warn' | 'critical'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Control Plane недоступен — панель показывает последний снимок (§10.5.9). */
    readonly offline = false,
  ) {
    super(message)
  }
}

const JSON_HEADERS = { Accept: 'application/json' }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'same-origin',
      headers: { ...JSON_HEADERS, ...(init?.headers ?? {}) },
    })
  } catch (cause) {
    // Сеть не ответила вовсе: для панели это «Control Plane не отвечает»,
    // а не «ошибка 0».
    throw new ApiError(0, 'Control Plane не отвечает', true)
  }

  if (response.status === 401) {
    throw new ApiError(401, 'Сессия истекла')
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new ApiError(response.status, detail.slice(0, 200) || `Ошибка ${response.status}`,
      response.status >= 502)
  }
  return (await response.json()) as T
}

export const api = {
  today: () => request<TodayPayload>('/api/panel/v1/today'),
  approvals: (state = 'pending') =>
    request<{ items: ApprovalBrief[] }>(`/api/panel/v1/approvals?state=${state}`),
  approval: (id: string) => request<ApprovalDetail>(`/api/panel/v1/approvals/${id}`),
  tasks: () => request<TaskGroups>('/api/panel/v1/tasks'),
  task: (id: string) => request<TaskDetail>(`/api/panel/v1/tasks/${id}`),
  money: () => request<MoneyPayload>('/api/panel/v1/money'),
  system: () => request<SystemPayload>('/api/panel/v1/system'),

  /** Санитизированный статус Guardian. Работает, когда Control Plane лежит. */
  guardianStatus: () =>
    request<{ status: Health; generated_at: string; degraded: boolean }>('/guardian/status.json'),

  /**
   * Решение по одобрению.
   *
   * `stepUpId` обязателен: свежая passkey-церемония, привязанная к этому
   * действию (§10.5.8.1). Никакого «запомнить на 30 дней» — параметр нельзя
   * не передать.
   */
  decide: (approvalId: string, decision: 'approve' | 'reject', stepUpId: string) =>
    request<{ status: string; result?: unknown }>(
      `/api/panel/v1/actions/${approvalId}/${decision}`,
      { method: 'POST', headers: { 'X-Helm-StepUp': stepUpId } },
    ),
}

// ── формы ответов ───────────────────────────────────────────────────────────

export interface ApprovalBrief {
  id: string
  short_id: string
  action_type: string
  title_ru: string
  panel_view: 'generic' | 'publication' | 'git_merge' | 'spend' | 'deploy'
  expires_at: string
  requested_at: string
  action_hash_short: string
  task_id: string | null
}

export interface Precondition {
  name: string
  ok: boolean
  detail: string | null
}

export interface ApprovalDetail extends ApprovalBrief {
  status: string
  action_hash: string
  payload: Record<string, unknown>
  preconditions: Precondition[]
  trust: { supervised_success: number; threshold: number; last_incident_at: string | null }
  decided_at: string | null
  decided_by_channel: string | null
}

export interface TodayPayload {
  generated_at: string
  approvals: { count: number; items: ApprovalBrief[] }
  money: {
    spent_today_usd: string | null
    hard_limit_usd: string | null
    kill_switch_active: boolean
  }
  tasks: { running: number; stuck: { id: string; title: string | null; stuck_since: string }[] }
  overnight: { id: string; title: string | null; finished_at: string }[]
}

export interface TaskRow {
  id: string
  title: string | null
  domain: string | null
  status: string
  since: string
}

export type TaskGroups = Record<'stuck' | 'running' | 'needs_approval' | 'done_today', TaskRow[]>

export interface TaskDetail {
  id: string
  title: string | null
  status: string
  domain: string | null
  risk_level: string | null
  timeline: { at: string; actor: string; event: string; payload: unknown }[]
  model_calls: {
    at: string
    alias: string | null
    model: string | null
    input_tokens: number | null
    output_tokens: number | null
    cost_usd: string | null
    reason_short: string | null
  }[]
}

export interface MoneyPayload {
  today: {
    spent_usd: string
    hard_limit_usd: string | null
    soft_limit_usd: string | null
    kill_switch_active: boolean
  }
  daily: { date: string; scope: string; spent_usd: string }[]
  expensive_calls: {
    at: string
    task_id: string | null
    alias: string | null
    model: string | null
    cost_usd: string | null
    reason_short: string | null
  }[]
}

export interface SystemPayload {
  resources: {
    metric: string
    value: string
    at: string
    labels: Record<string, unknown> | null
  }[]
  routines: {
    id: string
    name: string
    schedule: string
    enabled: boolean
    last_run_at: string | null
    last_status: string | null
    consecutive_failures: number
  }[]
}
