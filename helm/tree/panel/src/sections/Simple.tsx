/* Задачи, Деньги, Система (бриф §3.3–3.5).
 *
 * Три раздела в одном файле намеренно: каждый — это таблица над одним
 * эндпоинтом, и разносить их по файлам ради симметрии значило бы добавить
 * три уровня вложенности без единого нового поведения.
 */

import { api, type TaskGroups } from '../api/client'
import { useBlock } from '../api/useBlock'
import { Ago, Block, Empty, MetricRow, Mono } from '../components/primitives'

const GROUP_TITLES: Record<keyof TaskGroups, string> = {
  stuck: 'Застряли',
  running: 'В работе',
  needs_approval: 'Ждут одобрения',
  done_today: 'Готово сегодня',
}

export function Tasks() {
  const tasks = useBlock(() => api.tasks())
  const groups = tasks.data

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--h-block-gap)' }}>
      {(Object.keys(GROUP_TITLES) as (keyof TaskGroups)[]).map((key) => {
        const title = GROUP_TITLES[key]
        const rows = groups?.[key] ?? []
        return (
          <Block key={key} title={`${title}${rows.length ? ` · ${rows.length}` : ''}`}
                 error={tasks.error} offline={tasks.offline}
                 loadedAt={tasks.loadedAt} onRetry={tasks.reload}>
            {rows.length === 0 ? (
              <Empty>Пусто</Empty>
            ) : (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {rows.map((row) => (
                  <li key={row.id} style={{
                    display: 'flex', justifyContent: 'space-between', gap: 10,
                    minHeight: 'var(--h-row-min)', alignItems: 'center',
                  }}>
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: 'block', overflow: 'hidden',
                                     textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {row.title ?? 'без названия'}
                      </span>
                      <span style={{ fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>
                        {row.domain ?? 'без домена'} · <Mono value={row.id.slice(0, 8)} full={row.id} />
                      </span>
                    </span>
                    <Ago at={row.since} />
                  </li>
                ))}
              </ul>
            )}
          </Block>
        )
      })}
    </div>
  )
}

export function Money() {
  const money = useBlock(() => api.money())
  const data = money.data

  // График строится только при достаточных данных (бриф §2: график там, где
  // вопрос — тренд, и только при ≥7 точках).
  const series = (data?.daily ?? []).filter((row) => row.scope === 'system')
  const showChart = series.length >= 7

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--h-block-gap)' }}>
      <Block title="Сегодня и лимит" error={money.error} offline={money.offline}
             loadedAt={money.loadedAt} onRetry={money.reload}>
        {!data ? <Empty>Нет данных</Empty> : (
          <>
            <MetricRow label="Израсходовано сегодня"
                       value={`$${data.today.spent_usd} из $${data.today.hard_limit_usd ?? '—'}`} />
            {data.today.soft_limit_usd && (
              <MetricRow label="Мягкий порог" value={`$${data.today.soft_limit_usd}`} />
            )}
            <MetricRow label="Kill switch"
                       value={data.today.kill_switch_active ? 'включён' : 'выключен'}
                       tone={data.today.kill_switch_active ? 'critical' : 'ok'}
                       hint="изменение уходит в Одобрения" />
          </>
        )}
      </Block>

      <Block title="Расход за 30 дней" error={money.error} loadedAt={money.loadedAt}
             onRetry={money.reload}>
        {!showChart ? (
          <Empty>
            {series.length === 0
              ? 'Данных пока нет'
              : `Точек ${series.length} из 7 — график появится, когда накопится история`}
          </Empty>
        ) : (
          <DailyBars series={series} />
        )}
      </Block>

      <Block title="Дороже helm-standard" error={money.error} loadedAt={money.loadedAt}
             onRetry={money.reload}>
        {!data || data.expensive_calls.length === 0 ? (
          <Empty>Дорогих вызовов не было</Empty>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.expensive_calls.map((call, index) => (
              <li key={`${call.at}-${index}`} style={{ padding: '8px 0',
                    borderBottom: '1px solid var(--h-border)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <Mono value={`${call.alias ?? '—'} → ${call.model ?? '—'}`} />
                  <span className="h-mono">${call.cost_usd ?? '—'}</span>
                </div>
                {/* Единственный LLM-текст, разрешённый брифом §2: сохранённая
                    строка «почему вызвана дорогая модель». */}
                {call.reason_short && (
                  <p style={{ margin: '4px 0 0', color: 'var(--h-mut)' }}>{call.reason_short}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Block>
    </div>
  )
}

/** Столбчатый график на SVG — тяжёлая chart-библиотека не нужна (§10.5.2). */
function DailyBars({ series }: { series: { date: string; spent_usd: string }[] }) {
  const values = series.map((row) => Number(row.spent_usd) || 0)
  const max = Math.max(...values, 0.01)
  const width = 100
  const barWidth = width / series.length

  return (
    <figure style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${width} 40`} preserveAspectRatio="none" role="img"
           aria-label={`Расход по дням, максимум $${max.toFixed(2)}`}
           style={{ width: '100%', height: 80, display: 'block' }}>
        {series.map((row, index) => {
          const height = (Number(row.spent_usd) || 0) / max * 36
          return (
            <rect key={row.date} x={index * barWidth + barWidth * 0.15}
                  y={40 - height} width={barWidth * 0.7} height={Math.max(height, 0.5)}
                  fill="var(--h-acc)" opacity={0.85}>
              <title>{`${row.date}: $${row.spent_usd}`}</title>
            </rect>
          )
        })}
      </svg>
      <figcaption style={{ display: 'flex', justifyContent: 'space-between',
                           fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)', marginTop: 4 }}>
        <span>{series[0]?.date}</span>
        <span>максимум ${max.toFixed(2)}</span>
        <span>{series[series.length - 1]?.date}</span>
      </figcaption>
    </figure>
  )
}

export function System() {
  const system = useBlock(() => api.system())
  const guardian = useBlock(() => api.guardianStatus())
  const data = system.data

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--h-block-gap)' }}>
      <Block title="Сервер" error={system.error} offline={system.offline}
             loadedAt={system.loadedAt} onRetry={system.reload}>
        {!data || data.resources.length === 0 ? (
          <Empty>Guardian ещё не прислал метрик</Empty>
        ) : (
          data.resources.map((row) => (
            <MetricRow key={row.metric} label={row.metric} value={row.value}
                       hint={row.labels ? String((row.labels as Record<string, unknown>).detail ?? '') : undefined} />
          ))
        )}
      </Block>

      <Block title="Guardian" error={guardian.error} loadedAt={guardian.loadedAt}
             onRetry={guardian.reload}>
        {guardian.data ? (
          <MetricRow label="Статус" value={guardian.data.status}
                     tone={guardian.data.degraded ? 'warn' : 'ok'} />
        ) : (
          <Empty>Статус недоступен</Empty>
        )}
      </Block>

      <Block title="Рутины" error={system.error} loadedAt={system.loadedAt} onRetry={system.reload}>
        {!data || data.routines.length === 0 ? (
          <Empty>Рутин нет</Empty>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.routines.map((routine) => (
              <li key={routine.id} style={{
                display: 'flex', justifyContent: 'space-between', gap: 10,
                minHeight: 'var(--h-row-min)', alignItems: 'center',
              }}>
                <span>
                  <span style={{ display: 'block' }}>{routine.name}</span>
                  <span className="h-mono" style={{ fontSize: 'var(--h-fs-label)',
                                                    color: 'var(--h-faint)' }}>
                    {routine.schedule}
                  </span>
                </span>
                <span style={{
                  color: routine.consecutive_failures > 0 ? 'var(--h-crit)' : 'var(--h-mut)',
                }}>
                  {routine.enabled ? (routine.last_status ?? 'не запускалась') : 'выключена'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Block>
    </div>
  )
}
