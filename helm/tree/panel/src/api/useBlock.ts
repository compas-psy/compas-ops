/* Загрузка одного блока.
 *
 * Бриф §2: «ошибка загрузки блока показывается внутри блока, остальной экран
 * работает», и §5: «блок показывает последнее известное значение с пометкой
 * возраста». Поэтому хук держит последние удачные данные и при отказе не
 * обнуляет их — иначе экран мигал бы в пустоту при каждом сбое сети.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './client'

export interface BlockState<T> {
  data: T | null
  error: string | null
  offline: boolean
  /** Момент последнего УДАЧНОГО ответа — для пометки возраста данных. */
  loadedAt: Date | null
  loading: boolean
  reload: () => void
}

export function useBlock<T>(load: () => Promise<T>, deps: unknown[] = []): BlockState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [offline, setOffline] = useState(false)
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)
  const [loading, setLoading] = useState(true)
  const alive = useRef(true)

  const run = useCallback(() => {
    setLoading(true)
    load()
      .then((value) => {
        if (!alive.current) return
        setData(value)
        setError(null)
        setOffline(false)
        setLoadedAt(new Date())
      })
      .catch((cause: unknown) => {
        if (!alive.current) return
        const apiError = cause instanceof ApiError ? cause : null
        setError(apiError?.message ?? 'Не удалось загрузить')
        setOffline(apiError?.offline ?? false)
        // data намеренно не сбрасывается: последний снимок полезнее пустоты.
      })
      .finally(() => alive.current && setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    alive.current = true
    run()
    return () => {
      alive.current = false
    }
  }, [run])

  return { data, error, offline, loadedAt, loading, reload: run }
}
