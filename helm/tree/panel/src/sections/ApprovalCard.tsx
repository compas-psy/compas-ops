/* Карточка одобрения (бриф §3.2).
 *
 * Показывает суть действия в его родной форме: для публикации — полный текст
 * как он будет отправлен, для merge — файлы и diff-статистику, для трат —
 * сумму и получателя. Рендерер выбирается по panel_view из actions.yaml, а не
 * угадывается по имени действия.
 */

import { useState } from 'react'
import { api, type ApprovalDetail } from '../api/client'
import { useBlock } from '../api/useBlock'
import { PasskeyCancelled, stepUpFor } from '../components/passkey'
import { Ago, Block, Mono, PrimaryButton, SecondaryButton, TextButton } from '../components/primitives'

export function ApprovalCard({ id, onBack }: { id: string; onBack: () => void }) {
  const detail = useBlock(() => api.approval(id), [id])
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const decide = async (decision: 'approve' | 'reject') => {
    if (!detail.data) return
    setFailure(null)
    setBusy(true)
    try {
      const stepUp = await stepUpFor(detail.data.id, detail.data.action_hash)
      const result = await api.decide(detail.data.id, decision, stepUp)
      // Бриф §3.2: ответ — одна строка на той же карточке, без перехода.
      setOutcome(`${decision === 'approve' ? 'Выполнено' : 'Отклонено'} · ${new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })} · ${result.status}`)
      detail.reload()
    } catch (cause) {
      if (cause instanceof PasskeyCancelled) {
        setFailure(null)  // отмена — не ошибка, спрашивать дважды не нужно
      } else {
        // Бриф §6: истёкшее одобрение, несовпавший хэш и упавшее предусловие —
        // сообщение НА карточке, не тост.
        setFailure(cause instanceof Error ? cause.message : 'Действие не выполнено')
      }
    } finally {
      setBusy(false)
    }
  }

  const data = detail.data
  const blocked = data?.preconditions.some((p) => !p.ok) ?? false
  const decided = Boolean(data?.decided_at)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--h-block-gap)' }}>
      <TextButton onClick={onBack}>← Все ожидающие</TextButton>

      <Block title="Одобрение" error={detail.error} offline={detail.offline}
             loadedAt={detail.loadedAt} onRetry={detail.reload}>
        {!data ? null : (
          <>
            <div>
              <h3 style={{ margin: 0, fontSize: 'var(--h-fs-lead)' }}>{data.title_ru}</h3>
              <div style={{ marginTop: 2 }}>
                <Mono value={data.action_type} />
              </div>
            </div>

            <ActionGist detail={data} />

            <div>
              <p className="h-label" style={{ margin: '0 0 6px' }}>Предусловия</p>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {data.preconditions.length === 0 && (
                  <li style={{ color: 'var(--h-mut)' }}>Предусловий нет</li>
                )}
                {data.preconditions.map((precondition) => (
                  <li key={precondition.name} style={{
                    display: 'flex', gap: 8, alignItems: 'baseline', minHeight: 28,
                    color: precondition.ok ? 'var(--h-ink)' : 'var(--h-crit)',
                  }}>
                    <span aria-hidden style={{ width: 12 }}>{precondition.ok ? '✓' : '✕'}</span>
                    <span>
                      {/* Статус дублируется текстом: цвет и значок не должны
                          быть единственным способом его узнать. */}
                      <span className="h-sr">
                        {precondition.ok ? 'выполнено: ' : 'не выполнено: '}
                      </span>
                      <span className="h-mono">{precondition.name}</span>
                      {precondition.detail && (
                        <span style={{ display: 'block', fontSize: 'var(--h-fs-label)' }}>
                          {precondition.detail}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
                          fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>
              <span>хэш <Mono value={data.action_hash.slice(0, 12)} full={data.action_hash} /></span>
              <span>истекает <Ago at={data.expires_at} /></span>
            </div>

            {/* Уровень доверия — маленькая строка, без прогресс-бара (бриф §3.2) */}
            <p style={{ margin: 0, fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>
              доверие: {data.trust.supervised_success} из {data.trust.threshold} supervised
            </p>

            {outcome && <p role="status" style={{ margin: 0, color: 'var(--h-ok)' }}>{outcome}</p>}
            {failure && <p role="alert" style={{ margin: 0, color: 'var(--h-crit)' }}>{failure}</p>}

            {!decided && (
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <PrimaryButton onClick={() => decide('approve')} busy={busy} disabled={blocked}>
                  Одобрить
                </PrimaryButton>
                <SecondaryButton onClick={() => decide('reject')} disabled={busy}>
                  Отклонить
                </SecondaryButton>
              </div>
            )}
            {blocked && !decided && (
              <p style={{ margin: 0, fontSize: 'var(--h-fs-label)', color: 'var(--h-crit)' }}>
                Предусловие не выполнено — действие нельзя одобрить сейчас
              </p>
            )}
          </>
        )}
      </Block>
    </div>
  )
}

/** Суть действия в его родной форме (бриф §3.2), по panel_view из policy. */
function ActionGist({ detail }: { detail: ApprovalDetail }) {
  const payload = detail.payload
  const surface = {
    background: 'var(--h-card2)', borderRadius: 'var(--h-radius-sm)', padding: 12,
    margin: 0, whiteSpace: 'pre-wrap' as const, wordBreak: 'break-word' as const,
  }

  if (detail.panel_view === 'publication') {
    return (
      <div>
        <p className="h-label" style={{ margin: '0 0 6px' }}>
          Текст как будет отправлен · {String(payload.channel ?? '—')}
        </p>
        <p style={surface}>{String(payload.body ?? '')}</p>
        <p style={{ margin: '4px 0 0', fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>
          {String(payload.body ?? '').length} знаков
        </p>
      </div>
    )
  }

  if (detail.panel_view === 'spend') {
    return (
      <div>
        <p className="h-label" style={{ margin: '0 0 6px' }}>Трата</p>
        <div style={surface}>
          <div className="h-mono" style={{ fontSize: 'var(--h-fs-hero)' }}>
            {String(payload.amount ?? '—')} {String(payload.currency ?? '')}
          </div>
          <div style={{ color: 'var(--h-mut)' }}>
            получатель: {String(payload.recipient ?? '—')}
          </div>
          <div style={{ color: 'var(--h-mut)' }}>
            назначение: {String(payload.purpose ?? '—')}
          </div>
        </div>
      </div>
    )
  }

  if (detail.panel_view === 'git_merge' || detail.panel_view === 'deploy') {
    const files = Array.isArray(payload.files) ? (payload.files as string[]) : []
    return (
      <div>
        <p className="h-label" style={{ margin: '0 0 6px' }}>Изменения</p>
        <div style={surface}>
          <div><Mono value={String(payload.head_sha ?? '—')} full={String(payload.head_sha ?? '')} /></div>
          {files.length > 0 && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 16 }}>
              {files.slice(0, 12).map((file) => (
                <li key={file} className="h-mono">{file}</li>
              ))}
              {files.length > 12 && <li style={{ color: 'var(--h-faint)' }}>и ещё {files.length - 12}</li>}
            </ul>
          )}
        </div>
      </div>
    )
  }

  // generic: показываем сохранённый payload как есть, без интерпретации.
  return (
    <div>
      <p className="h-label" style={{ margin: '0 0 6px' }}>Параметры</p>
      <pre style={{ ...surface, fontFamily: 'var(--h-mono)', fontSize: 12, overflowX: 'auto' }}>
        {JSON.stringify(payload, null, 2)}
      </pre>
    </div>
  )
}
