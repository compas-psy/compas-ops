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
    // Нет валидной сессии — единственное осмысленное действие панели: начать
    // вход заново. Показывать «сессия истекла» и ждать повторного нажатия
    // некуда, раздела для этого в панели нет (бриф §3). /login — экран со
    // встроенным Telegram Login Widget (§10.5.6), не серверный редирект.
    window.location.href = '/login'
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

  /** Пользователи Второго мозга — метаданные и квоты, не их содержимое. */
  knowledgeUsers: () => request<{ items: KnowledgeUserRow[] }>('/api/panel/v1/users'),

  /**
   * Пригласить нового пользователя. Ответ содержит одноразовую ссылку —
   * второй раз её узнать негде: в базе только хэш токена.
   */
  inviteKnowledgeUser: (body: { display_name?: string }, stepUpId: string) =>
    request<KnowledgeInvite>('/api/panel/v1/users/invite', {
      method: 'POST',
      headers: { 'X-Helm-StepUp': stepUpId, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  setKnowledgeUserAccess: (userId: string, action: 'suspend' | 'reactivate', stepUpId: string) =>
    request<{ status: string }>(`/api/panel/v1/users/${userId}/${action}`, {
      method: 'POST', headers: { 'X-Helm-StepUp': stepUpId },
    }),

  /**
   * Одноразовый токен доступа в панель для KNOWLEDGE_USER. Владелец
   * передаёт его человеку сам — HELM за этот канал не отвечает, поэтому
   * срок короткий и токен одноразовый.
   */
  invitePanelAccess: (userId: string, stepUpId: string) =>
    request<{ enrollment_token: string; panel_url: string; expires_at: string }>(
      `/api/panel/v1/users/${userId}/panel-invite`,
      { method: 'POST', headers: { 'X-Helm-StepUp': stepUpId } },
    ),

  /**
   * Выгрузка Второго мозга человека перед offboarding'ом. Возвращает путь
   * к файлу на сервере, а не содержимое: панель чужой Второй мозг не
   * показывает.
   */
  exportKnowledgeUser: (userId: string, stepUpId: string) =>
    request<{ archive_path: string; memories: number; sources: number; backup_retention: string }>(
      `/api/panel/v1/users/${userId}/export`,
      { method: 'POST', headers: { 'X-Helm-StepUp': stepUpId } },
    ),

  /** Необратимо. Требует заранее приостановленного аккаунта. */
  deleteKnowledgeUser: (userId: string, exportTaken: boolean, stepUpId: string) =>
    request<{ rows_deleted: number; backup_retention: string }>(
      `/api/panel/v1/users/${userId}/delete`,
      {
        method: 'POST',
        headers: { 'X-Helm-StepUp': stepUpId, 'Content-Type': 'application/json' },
        body: JSON.stringify({ export_taken: exportTaken }),
      },
    ),

  /** Какую оболочку рисовать: владельца или Knowledge-only. */
  session: () => request<{ role: PanelRole }>('/api/panel/v1/session'),

  /**
   * Исходные байты документа (§14.15). POST, а не ссылка: свежее
   * passkey-подтверждение приходит заголовком, которого у обычной
   * ссылки нет. Возвращает сам файл, поэтому идёт мимо request().
   */
  downloadSource: async (sourceId: string, stepUpId: string): Promise<{ blob: Blob; filename: string }> => {
    const response = await fetch(`/api/panel/v1/knowledge/sources/${sourceId}/download`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Helm-StepUp': stepUpId },
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new ApiError(response.status, detail.slice(0, 200) || 'Не удалось скачать')
    }
    const disposition = response.headers.get('Content-Disposition') ?? ''
    const match = /filename="([^"]+)"/.exec(disposition)
    return { blob: await response.blob(), filename: match?.[1] ?? sourceId }
  },

  /** Свой Второй мозг — и ничей больше: тенант берётся из сессии. */
  knowledge: () => request<KnowledgeShell>('/api/panel/v1/knowledge'),
}

export type PanelRole = 'SYSTEM_OWNER' | 'KNOWLEDGE_USER'

/**
 * Ответ Knowledge-оболочки. Полей, ссылающихся на чужого пользователя,
 * здесь нет по построению: сервер не принимает knowledge_user_id
 * параметром, он берёт его из сессии.
 */
export interface KnowledgeShell {
  role: PanelRole | null
  display_name: string | null
  timezone: string | null
  usage: {
    storage_bytes: number
    sources_count: number
    memories_count: number
    storage_quota_bytes: number | null
  }
  memories: {
    id: string
    kind: string
    text: string
    status: string
    created_at: string
    expires_at: string | null
  }[]
  sources: {
    id: string
    title: string | null
    domain: string | null
    status: string
    created_at: string
  }[]
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

/**
 * Строка раздела «Пользователи» (v3.8 §14.3). Полей с содержимым Второго
 * мозга здесь нет и не должно появиться: спека прямо запрещает владельцу
 * «normal content browser across users» — раздел управляет доступом, а не
 * читает чужие документы и память.
 */
export interface KnowledgeUserRow {
  id: string
  role: 'SYSTEM_OWNER' | 'KNOWLEDGE_USER'
  status: 'INVITED' | 'ACTIVE' | 'SUSPENDED' | 'DELETED'
  display_name: string | null
  locale: string
  timezone: string
  allow_paid_ai: boolean
  storage_quota_bytes: number | null
  daily_ingest_quota_bytes: number | null
  storage_bytes: number
  ingest_bytes_today: number
  created_at: string
  activated_at: string | null
  suspended_at: string | null
  channels: { channel: string; verified_at: string; is_primary: boolean }[]
}

export interface KnowledgeInvite {
  knowledge_user_id: string
  invite_token: string
  deep_link: string | null
  expires_at: string
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
