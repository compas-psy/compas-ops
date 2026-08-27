/* Экран «Сегодня» (бриф §3.1).
 *
 * Одно вертикальное полотно, читается без скролла на 390×844 в состоянии
 * «всё в порядке, 0 одобрений». Порядок блоков — по срочности и он
 * фиксирован: блок «Ждут вас» не исчезает при нуле, потому что постоянство
 * раскладки важнее экономии места (бриф §3.1 п.2).
 */

import { api, type Health } from '../api/client'
import { useBlock } from '../api/useBlock'
import { Ago, Block, Empty, MetricRow, Mono, SecondaryButton, TextButton } from '../components/primitives'

export function Today({ onOpenApproval, onGoTo }: {
  onOpenApproval: (id: string) => void
  onGoTo: (section: string) => void
}) {
  const today = useBlock(() => api.today())
  const guardian = useBlock(() => api.guardianStatus())

  // Состояние системы: если Control Plane молчит, слово берётся у Guardian —
  // он отдаёт статический JSON независимо (бриф §6).
  const health: Health = guardian.data?.status ?? (today.offline ? 'critical' : 'ok')
  const words: Record<Health, string> = { ok: 'В порядке', warn: 'Внимание', critical: 'Проблема' }
  const tones: Record<Health, string> = {
    ok: 'transparent', warn: 'var(--h-warn-soft)', critical: 'var(--h-crit-soft)',
  }
  const inks: Record<Health, string> = {
    ok: 'var(--h-mut)', warn: 'var(--h-warn)', critical: 'var(--h-crit)',
  }

  const data = today.data

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--h-block-gap)' }}>
      {/* 1. Строка состояния — текст с цветом фона строки, не светофор-кружок */}
      <div role="status" style={{
        background: tones[health], color: inks[health],
        border: health === 'ok' ? '1px solid var(--h-border)' : 'none',
        borderRadius: 'var(--h-radius-sm)', padding: '10px 14px',
        minHeight: 'var(--h-row-min)', display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', gap: 10,
      }}>
        <span style={{ fontSize: 'var(--h-fs-lead)', fontWeight: 700 }}>{words[health]}</span>
        {today.offline && (
          <TextButton onClick={() => { today.reload(); guardian.reload() }}>
            Control Plane не отвечает
          </TextButton>
        )}
      </div>

      {/* 2. Ждут вас */}
      <Block title="Ждут вас" error={today.error} offline={today.offline}
             loadedAt={today.loadedAt} onRetry={today.reload}
             action={data && data.approvals.count > 3
               ? <TextButton onClick={() => onGoTo('approvals')}>Все {data.approvals.count}</TextButton>
               : undefined}>
        {!data || data.approvals.count === 0 ? (
          <Empty>Нет ожидающих одобрений</Empty>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.approvals.items.map((item) => (
              <li key={item.id} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                gap: 10, minHeight: 'var(--h-row-min)',
              }}>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'block', fontWeight: 700, overflow: 'hidden',
                                 textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.title_ru}
                  </span>
                  <span style={{ display: 'block', fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>
                    истекает <Ago at={item.expires_at} /> · <Mono value={item.action_hash_short} />
                  </span>
                </span>
                {/* Одобрение из списка требует passkey — поэтому «Открыть», а
                    не кнопка «Одобрить» здесь: церемония привязывается к
                    конкретному действию и делается на карточке. */}
                <SecondaryButton onClick={() => onOpenApproval(item.id)}>Открыть</SecondaryButton>
              </li>
            ))}
          </ul>
        )}
      </Block>

      {/* 3. Деньги сегодня — одна строка */}
      <Block title="Деньги сегодня" error={today.error} loadedAt={today.loadedAt}
             onRetry={today.reload}
             action={<TextButton onClick={() => onGoTo('money')}>Открыть</TextButton>}>
        {data?.money.spent_today_usd ? (
          <MetricRow
            label="Израсходовано"
            value={`$${data.money.spent_today_usd} из $${data.money.hard_limit_usd ?? '—'}`}
            tone={data.money.kill_switch_active ? 'critical' : 'ok'}
            hint={data.money.kill_switch_active ? 'Kill switch активен' : undefined}
          />
        ) : (
          <Empty>Данных о расходе нет</Empty>
        )}
      </Block>

      {/* 4. Задачи */}
      <Block title="Задачи" error={today.error} loadedAt={today.loadedAt} onRetry={today.reload}
             action={<TextButton onClick={() => onGoTo('tasks')}>Открыть</TextButton>}>
        {data ? (
          <>
            <MetricRow label="В работе" value={String(data.tasks.running)}
                       tone={data.tasks.stuck.length > 0 ? 'warn' : 'ok'} />
            {data.tasks.stuck.map((task) => (
              <MetricRow key={task.id} label={task.title ?? 'без названия'} tone="warn"
                         value="застряла"
                         hint={`без heartbeat с ${new Date(task.stuck_since).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`} />
            ))}
          </>
        ) : (
          <Empty>Нет данных</Empty>
        )}
      </Block>

      {/* 5. Сервер — только то, у чего есть порог */}
      <ServerBlock onGoTo={onGoTo} />

      {/* 6. Ночью сделано — без LLM-пересказа */}
      <Block title="Ночью сделано" error={today.error} loadedAt={today.loadedAt} onRetry={today.reload}>
        {!data || data.overnight.length === 0 ? (
          <Empty>С 22:00 задач не завершено</Empty>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.overnight.slice(0, 3).map((task) => (
              <li key={task.id} style={{
                display: 'flex', justifyContent: 'space-between', gap: 10,
                minHeight: 40, alignItems: 'center',
              }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {task.title ?? 'без названия'}
                </span>
                <Ago at={task.finished_at} />
              </li>
            ))}
          </ul>
        )}
      </Block>
    </div>
  )
}

function ServerBlock({ onGoTo }: { onGoTo: (section: string) => void }) {
  const system = useBlock(() => api.system())
  const rows = system.data?.resources ?? []
  const find = (metric: string) => rows.find((r) => r.metric === metric)

  return (
    <Block title="Сервер" error={system.error} offline={system.offline}
           loadedAt={system.loadedAt} onRetry={system.reload}
           action={<TextButton onClick={() => onGoTo('system')}>Открыть</TextButton>}>
      {rows.length === 0 ? (
        <Empty>Guardian ещё не прислал метрик</Empty>
      ) : (
        <>
          {(['disk', 'ram', 'backup_age'] as const).map((metric) => {
            const row = find(metric)
            if (!row) return null
            const labels = { disk: 'Диск', ram: 'RAM', backup_age: 'Бэкап' } as const
            // Бриф §2 запрещает процент без абсолюта. Guardian присылает
            // абсолютное значение в labels.detail («41 / 100 GB») — показываем
            // именно его; процент без гигабайтов ничего не говорит о запасе.
            const absolute = row.labels
              ? String((row.labels as Record<string, unknown>).detail ?? '')
              : ''
            // Если Guardian не прислал абсолют — честнее сказать это, чем
            // показать голый процент: устав §5.1 и запрет брифа §2.
            const value = metric === 'backup_age'
              ? `${row.value} ч назад`
              : absolute !== '' ? absolute : 'абсолютное значение не пришло'
            return <MetricRow key={metric} label={labels[metric]} value={value} />
          })}
        </>
      )}
    </Block>
  )
}
