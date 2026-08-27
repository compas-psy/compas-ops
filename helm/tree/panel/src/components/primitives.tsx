/* Примитивы панели. Каждый несёт запрет из брифа §2 в своей сигнатуре.
 *
 * Здесь нет компонентов Card-внутри-Card, круговых индикаторов и
 * прогресс-колец — не потому что их «не стали делать», а потому что бриф их
 * запрещает, и отсутствие примитива делает нарушение заметным на ревью.
 */

import type { ReactNode } from 'react'

// ── время ───────────────────────────────────────────────────────────────────

/**
 * Относительное время с абсолютным по нажатию (бриф §2).
 * `title` даёт абсолютное значение на десктопе, `aria-label` — скринридеру.
 */
export function Ago({ at }: { at: string | Date | null }) {
  if (!at) return <span style={{ color: 'var(--h-faint)' }}>—</span>
  const date = typeof at === 'string' ? new Date(at) : at
  const absolute = date.toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' })
  return (
    <time dateTime={date.toISOString()} title={absolute} aria-label={absolute}
          style={{ color: 'var(--h-mut)' }}>
      {formatAgo(date)}
    </time>
  )
}

export function formatAgo(date: Date, now = new Date()): string {
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000)
  const future = seconds < 0
  const abs = Math.abs(seconds)
  const suffix = future ? '' : ' назад'
  const prefix = future ? 'через ' : ''
  if (abs < 60) return future ? 'меньше минуты' : 'только что'
  if (abs < 3600) return `${prefix}${Math.round(abs / 60)} мин${suffix}`
  if (abs < 86400) return `${prefix}${Math.round(abs / 3600)} ч${suffix}`
  return `${prefix}${Math.round(abs / 86400)} дн${suffix}`
}

// ── идентификаторы ──────────────────────────────────────────────────────────

/** Моноширинный идентификатор, копируется одним нажатием (бриф §2). */
export function Mono({ value, full }: { value: string; full?: string }) {
  const copy = () => void navigator.clipboard?.writeText(full ?? value)
  return (
    <button type="button" onClick={copy} className="h-mono"
            title={`${full ?? value} — нажмите, чтобы скопировать`}
            style={{
              background: 'none', border: 'none', padding: '2px 4px', margin: '-2px -4px',
              color: 'var(--h-mut)', cursor: 'pointer', font: 'inherit',
            }}>
      {value}
    </button>
  )
}

// ── блок ────────────────────────────────────────────────────────────────────

export interface BlockProps {
  title: string
  children: ReactNode
  /** Ошибка показывается ВНУТРИ блока; остальной экран продолжает работать. */
  error?: string | null
  offline?: boolean
  loadedAt?: Date | null
  onRetry?: () => void
  action?: ReactNode
}

export function Block({ title, children, error, offline, loadedAt, onRetry, action }: BlockProps) {
  const stale = Boolean(error && loadedAt)
  return (
    <section
      aria-labelledby={`block-${slug(title)}`}
      style={{
        background: 'var(--h-card)',
        border: '1px solid var(--h-border)',
        borderRadius: 'var(--h-radius)',
        padding: 'var(--h-block-pad)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--h-block-gap)',
      }}
    >
      <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
        <h2 id={`block-${slug(title)}`} className="h-label" style={{ margin: 0 }}>{title}</h2>
        {action}
      </header>

      {stale && (
        // Бриф §5: устаревший блок показывает возраст данных, а не спиннер.
        <p role="status" style={{ margin: 0, fontSize: 'var(--h-fs-label)', color: 'var(--h-warn)' }}>
          {offline ? 'Control Plane не отвечает. ' : ''}
          Данные от {loadedAt!.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
        </p>
      )}

      {error && !loadedAt ? (
        <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: 'var(--h-mut)' }}>{error}</span>
          {onRetry && <TextButton onClick={onRetry}>Повторить</TextButton>}
        </div>
      ) : (
        children
      )}
    </section>
  )
}

function slug(value: string) {
  return value.toLowerCase().replace(/[^a-zа-яё0-9]+/gi, '-')
}

// ── строки ──────────────────────────────────────────────────────────────────

/**
 * Строка с показателем. Абсолютное значение обязательно (бриф §2:
 * «проценты без абсолюта» запрещены), поэтому `value` не опционален.
 */
export function MetricRow({ label, value, tone = 'ok', hint }: {
  label: string
  value: string
  tone?: 'ok' | 'warn' | 'critical'
  hint?: string
}) {
  const background =
    tone === 'critical' ? 'var(--h-crit-soft)' : tone === 'warn' ? 'var(--h-warn-soft)' : 'transparent'
  const color = tone === 'critical' ? 'var(--h-crit)' : tone === 'warn' ? 'var(--h-warn)' : 'var(--h-ink)'
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10,
      minHeight: 'var(--h-row-min)', padding: background === 'transparent' ? '0' : '0 10px',
      margin: background === 'transparent' ? 0 : '0 -10px',
      background, borderRadius: 10,
    }}>
      <span style={{ color: 'var(--h-mut)' }}>{label}</span>
      <span style={{ color, textAlign: 'right' }}>
        <span className="h-mono" style={{ fontSize: 'var(--h-fs-body)' }}>{value}</span>
        {hint && <span style={{ display: 'block', fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>{hint}</span>}
      </span>
    </div>
  )
}

/** Пустое состояние — факт, а не утешение (бриф §2). */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <p style={{ margin: 0, color: 'var(--h-mut)', minHeight: 'var(--h-row-min)',
                display: 'flex', alignItems: 'center' }}>
      {children}
    </p>
  )
}

// ── действия ────────────────────────────────────────────────────────────────

export function PrimaryButton({ children, onClick, disabled, busy }: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  busy?: boolean
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled || busy}
            style={{
              minHeight: 'var(--h-row-min)', padding: '0 18px',
              background: 'var(--h-acc)', color: 'var(--h-acc-ink)',
              border: 'none', borderRadius: 'var(--h-radius-sm)',
              fontFamily: 'inherit', fontSize: 'var(--h-fs-body)',
              fontWeight: 'var(--h-fw-bold)' as never,
              cursor: disabled || busy ? 'default' : 'pointer',
              opacity: disabled ? 0.5 : 1,
              transition: `opacity var(--h-motion) ease`,
            }}>
      {busy ? 'Подтвердите passkey…' : children}
    </button>
  )
}

export function SecondaryButton({ children, onClick, disabled }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
            style={{
              minHeight: 'var(--h-row-min)', padding: '0 16px',
              background: 'transparent', color: 'var(--h-ink)',
              border: '1px solid var(--h-border)', borderRadius: 'var(--h-radius-sm)',
              fontFamily: 'inherit', fontSize: 'var(--h-fs-body)',
              cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1,
            }}>
      {children}
    </button>
  )
}

export function TextButton({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <button type="button" onClick={onClick}
            style={{
              background: 'none', border: 'none', padding: 0, font: 'inherit',
              color: 'var(--h-acc)', cursor: 'pointer', textDecoration: 'underline',
              textUnderlineOffset: 2,
            }}>
      {children}
    </button>
  )
}
