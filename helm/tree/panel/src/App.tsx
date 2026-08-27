/* Оболочка панели (бриф §4).
 *
 * Телефон — нижняя панель на 5 пунктов со счётчиком одобрений.
 * Ноутбук — левая узкая колонка с теми же пятью пунктами: та же информационная
 * модель, не другой интерфейс. Глубина навигации ≤ 2: раздел → карточка.
 */

import { useEffect, useState } from 'react'
import { api } from './api/client'
import { useBlock } from './api/useBlock'
import { ApprovalCard } from './sections/ApprovalCard'
import { Money, System, Tasks } from './sections/Simple'
import { Today } from './sections/Today'
import { Block, Empty, SecondaryButton } from './components/primitives'

const SECTIONS = [
  { id: 'today', label: 'Сегодня' },
  { id: 'approvals', label: 'Одобрения' },
  { id: 'tasks', label: 'Задачи' },
  { id: 'money', label: 'Деньги' },
  { id: 'system', label: 'Система' },
] as const

type SectionId = (typeof SECTIONS)[number]['id']

export function App() {
  const [section, setSection] = useState<SectionId>('today')
  const [openApproval, setOpenApproval] = useState<string | null>(null)
  const [wide, setWide] = useState(() => window.matchMedia('(min-width: 900px)').matches)

  useEffect(() => {
    const query = window.matchMedia('(min-width: 900px)')
    const onChange = (event: MediaQueryListEvent) => setWide(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const pending = useBlock(() => api.approvals('pending'))
  const pendingCount = pending.data?.items.length ?? 0

  const goTo = (next: string) => {
    setOpenApproval(null)
    setSection(next as SectionId)
  }

  const content = openApproval ? (
    <ApprovalCard id={openApproval} onBack={() => setOpenApproval(null)} />
  ) : section === 'today' ? (
    <Today onOpenApproval={setOpenApproval} onGoTo={goTo} />
  ) : section === 'approvals' ? (
    <ApprovalsList onOpen={setOpenApproval} />
  ) : section === 'tasks' ? (
    <Tasks />
  ) : section === 'money' ? (
    <Money />
  ) : (
    <System />
  )

  const title = SECTIONS.find((s) => s.id === section)?.label ?? ''

  return (
    <div style={{
      display: wide ? 'grid' : 'block',
      gridTemplateColumns: wide ? '200px minmax(0, 1fr)' : undefined,
      minHeight: '100vh',
    }}>
      {wide && (
        <nav aria-label="Разделы" style={{
          borderRight: '1px solid var(--h-border)', padding: 14,
          display: 'flex', flexDirection: 'column', gap: 4, position: 'sticky',
          top: 0, alignSelf: 'start', height: '100vh',
        }}>
          <p className="h-label" style={{ margin: '0 0 10px' }}>HELM</p>
          {SECTIONS.map((item) => (
            <NavItem key={item.id} label={item.label} active={section === item.id}
                     count={item.id === 'approvals' ? pendingCount : 0}
                     onClick={() => goTo(item.id)} rail />
          ))}
        </nav>
      )}

      <main style={{
        maxWidth: 720, margin: '0 auto', width: '100%',
        padding: wide ? '18px 18px 24px' : '14px 14px calc(var(--h-nav-h) + 24px)',
      }}>
        {/* Заголовок раздела сверху, без hamburger (бриф §4) */}
        <h1 style={{ margin: '0 0 12px', fontSize: 'var(--h-fs-hero)', letterSpacing: '-.02em' }}>
          {openApproval ? 'Одобрение' : title}
        </h1>
        {content}
      </main>

      {!wide && (
        <nav aria-label="Разделы" style={{
          position: 'fixed', left: 0, right: 0, bottom: 0,
          height: `calc(var(--h-nav-h) + env(safe-area-inset-bottom))`,
          paddingBottom: 'env(safe-area-inset-bottom)',
          background: 'var(--h-card)', borderTop: '1px solid var(--h-border)',
          display: 'grid', gridTemplateColumns: `repeat(${SECTIONS.length}, 1fr)`,
        }}>
          {SECTIONS.map((item) => (
            <NavItem key={item.id} label={item.label} active={section === item.id}
                     count={item.id === 'approvals' ? pendingCount : 0}
                     onClick={() => goTo(item.id)} />
          ))}
        </nav>
      )}
    </div>
  )
}

function NavItem({ label, active, count, onClick, rail }: {
  label: string; active: boolean; count: number; onClick: () => void; rail?: boolean
}) {
  return (
    <button type="button" onClick={onClick} aria-current={active ? 'page' : undefined}
            style={{
              display: 'flex', alignItems: 'center',
              justifyContent: rail ? 'space-between' : 'center',
              gap: 6, minHeight: rail ? 'var(--h-row-min)' : '100%',
              padding: rail ? '0 10px' : 0,
              background: active && rail ? 'var(--h-card2)' : 'none',
              border: 'none', borderRadius: rail ? 10 : 0,
              fontFamily: 'inherit', fontSize: rail ? 'var(--h-fs-body)' : 'var(--h-fs-label)',
              fontWeight: active ? 700 : 500,
              color: active ? 'var(--h-acc)' : 'var(--h-mut)',
              cursor: 'pointer',
            }}>
      <span>{label}</span>
      {count > 0 && (
        <span aria-label={`${count} ожидают`} className="h-mono" style={{
          fontSize: 10, minWidth: 18, height: 18, borderRadius: 9,
          background: 'var(--h-acc)', color: 'var(--h-acc-ink)',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          padding: '0 5px',
        }}>
          {count}
        </span>
      )}
    </button>
  )
}

function ApprovalsList({ onOpen }: { onOpen: (id: string) => void }) {
  const pending = useBlock(() => api.approvals('pending'))
  const items = pending.data?.items ?? []

  return (
    <Block title={`Ожидают${items.length ? ` · ${items.length}` : ''}`}
           error={pending.error} offline={pending.offline}
           loadedAt={pending.loadedAt} onRetry={pending.reload}>
      {items.length === 0 ? (
        <Empty>Нет ожидающих одобрений</Empty>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0,
                     display: 'flex', flexDirection: 'column', gap: 8 }}>
          {items.map((item) => (
            <li key={item.id} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              gap: 10, minHeight: 'var(--h-row-min)',
            }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 700, overflow: 'hidden',
                               textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {item.title_ru}
                </span>
                <span className="h-mono" style={{ fontSize: 'var(--h-fs-label)',
                                                  color: 'var(--h-faint)' }}>
                  {item.action_type}
                </span>
              </span>
              <SecondaryButton onClick={() => onOpen(item.id)}>Открыть</SecondaryButton>
            </li>
          ))}
        </ul>
      )}
    </Block>
  )
}
