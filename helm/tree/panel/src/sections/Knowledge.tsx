/* Knowledge-оболочка KNOWLEDGE_USER (ТЗ v3.8 §14.3, P8.6.5).
 *
 * «Own memories/docs/archives/taxonomy/settings only» — и ровно поэтому
 * здесь нет ни одного элемента управления чужими данными и ни одного
 * параметра, куда можно было бы подставить чужой идентификатор: сервер
 * берёт тенанта из сессии, фронт не выбирает его вовсе.
 *
 * Владелец видит этот же экран со своим корпусом — это заодно и есть
 * первая версия «Panel строка Knowledge» (P8.5.8) для него.
 */

import { useState } from 'react'

import { api } from '../api/client'
import { useBlock } from '../api/useBlock'
import { PasskeyCancelled, stepUpForScope } from '../components/passkey'
import { Ago, Block, Empty, MetricRow, Mono, SecondaryButton } from '../components/primitives'

function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}

export function Knowledge() {
  const shell = useBlock(() => api.knowledge())
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const data = shell.data

  // §14.15: отдаются исходные байты, а не пересказ. Файл приходит ответом
  // на POST (свежий passkey — заголовком), поэтому сохраняем его сами.
  const onDownload = async (sourceId: string) => {
    setBusy(sourceId)
    setError(null)
    try {
      const stepUpId = await stepUpForScope(`panel:knowledge:download:${sourceId}`)
      const { blob, filename } = await api.downloadSource(sourceId, stepUpId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      link.click()
      URL.revokeObjectURL(url)
    } catch (cause) {
      if (!(cause instanceof PasskeyCancelled)) {
        setError(cause instanceof Error ? cause.message : 'Не удалось скачать')
      }
    } finally {
      setBusy(null)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--h-block-gap)' }}>
      <Block title="Место" error={shell.error} offline={shell.offline}
             loadedAt={shell.loadedAt} onRetry={shell.reload}>
        {data ? (
          <>
            <MetricRow
              label="Занято"
              value={data.usage.storage_quota_bytes === null
                ? megabytes(data.usage.storage_bytes)
                : `${megabytes(data.usage.storage_bytes)} из ${megabytes(data.usage.storage_quota_bytes)}`}
            />
            <MetricRow label="Документов" value={String(data.usage.sources_count)} />
            <MetricRow label="Записей памяти" value={String(data.usage.memories_count)} />
          </>
        ) : (
          <Empty>Нет данных</Empty>
        )}
      </Block>

      <Block title={`Память${data?.memories.length ? ` · ${data.memories.length}` : ''}`}
             error={shell.error} loadedAt={shell.loadedAt} onRetry={shell.reload}>
        {!data || data.memories.length === 0 ? (
          <Empty>Пока ничего не запомнено. Напишите боту «Запомни …».</Empty>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.memories.map((memory) => (
              <li key={memory.id} style={{
                display: 'flex', justifyContent: 'space-between', gap: 10,
                minHeight: 'var(--h-row-min)', alignItems: 'center',
              }}>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'block', overflow: 'hidden',
                                 textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {memory.text}
                  </span>
                  <span style={{ fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>
                    {memory.kind === 'bookmark' ? 'ссылка' : 'заметка'}
                    {memory.expires_at && ' · временная'}
                    {memory.status !== 'ACTIVE' && ` · ${memory.status.toLowerCase()}`}
                  </span>
                </span>
                <Ago at={memory.created_at} />
              </li>
            ))}
          </ul>
        )}
      </Block>

      <Block title={`Документы${data?.sources.length ? ` · ${data.sources.length}` : ''}`}
             error={shell.error} loadedAt={shell.loadedAt} onRetry={shell.reload}>
        {error && <p style={{ color: 'var(--h-crit)', margin: '0 0 10px' }}>{error}</p>}
        {!data || data.sources.length === 0 ? (
          <Empty>Документов пока нет</Empty>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.sources.map((source) => (
              <li key={source.id} style={{
                display: 'flex', justifyContent: 'space-between', gap: 10,
                minHeight: 'var(--h-row-min)', alignItems: 'center',
              }}>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'block', overflow: 'hidden',
                                 textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {source.title ?? <Mono value={source.id.slice(0, 8)} full={source.id} />}
                  </span>
                  <span style={{ fontSize: 'var(--h-fs-label)', color: 'var(--h-faint)' }}>
                    {source.domain ?? 'без домена'} · {source.status.toLowerCase()}
                  </span>
                </span>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
                  <Ago at={source.created_at} />
                  <SecondaryButton onClick={() => onDownload(source.id)}
                                   disabled={busy !== null}>
                    Скачать оригинал
                  </SecondaryButton>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Block>
    </div>
  )
}
