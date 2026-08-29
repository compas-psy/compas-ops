/* Вход и первый enrollment passkey (ТЗ §10.5.6-§10.5.7).
 *
 * Существует только ради двух шагов, на которые Telegram OIDC callback
 * назначает редирект: ?step=enroll (первого passkey ещё нет) и ?step=login
 * (passkey уже есть, нужен ассершн). Оба заканчиваются одинаково —
 * появляется helm_panel_session cookie, и страница уходит на /.
 */

import { useState, type ReactNode } from 'react'
import { fromBase64Url, toBase64Url } from './api/codec'
import { PrimaryButton } from './components/primitives'

const PUB_KEY_ALGS: PublicKeyCredentialParameters[] = [
  { type: 'public-key', alg: -8 }, // EdDSA
  { type: 'public-key', alg: -7 }, // ES256
  { type: 'public-key', alg: -257 }, // RS256
]

export function Login() {
  const step = new URLSearchParams(window.location.search).get('step')
  if (step === 'enroll') return <Enroll />
  if (step === 'login') return <PasskeyLoginScreen />
  return <StartScreen />
}

function StartScreen() {
  return (
    <Screen title="Вход в HELM">
      <a
        href="/auth/telegram/start"
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          minHeight: 'var(--h-row-min)', padding: '0 18px',
          background: 'var(--h-acc)', color: 'var(--h-acc-ink)',
          borderRadius: 'var(--h-radius-sm)', textDecoration: 'none',
          fontWeight: 'var(--h-fw-bold)' as never,
        }}
      >
        Войти через Telegram
      </a>
    </Screen>
  )
}

function Enroll() {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const optionsResponse = await fetch('/auth/passkey/register/options', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enrollment_token: token }),
      })
      if (!optionsResponse.ok) throw new Error('Токен не принят')
      const options = (await optionsResponse.json()) as {
        challenge: string
        rp_id: string
        rp_name: string
        user_id: string
        user_name: string
        timeout_ms: number
      }

      const credential = (await navigator.credentials.create({
        publicKey: {
          challenge: fromBase64Url(options.challenge),
          rp: { id: options.rp_id, name: options.rp_name },
          user: {
            id: fromBase64Url(options.user_id),
            name: options.user_name,
            displayName: options.user_name,
          },
          pubKeyCredParams: PUB_KEY_ALGS,
          timeout: options.timeout_ms,
          // §10.5.7: userVerification обязателен уже на этапе первого enrollment.
          authenticatorSelection: { userVerification: 'required' },
        },
      })) as PublicKeyCredential | null
      if (!credential) throw new Error('Регистрация отменена')

      const response = credential.response as AuthenticatorAttestationResponse
      const verify = await fetch('/auth/passkey/register/verify', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          credential_id: toBase64Url(credential.rawId),
          client_data: toBase64Url(response.clientDataJSON),
          attestation_object: toBase64Url(response.attestationObject),
        }),
      })
      if (!verify.ok) throw new Error('Passkey не подтверждён')
      window.location.href = '/'
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось создать passkey')
      setBusy(false)
    }
  }

  return (
    <Screen title="Первый passkey">
      <p style={{ margin: 0, color: 'var(--h-mut)' }}>
        Telegram подтверждён. Введите одноразовый enrollment-токен, выданный при настройке.
      </p>
      <input
        value={token}
        onChange={(event) => setToken(event.target.value)}
        placeholder="Enrollment-токен"
        style={{
          minHeight: 'var(--h-row-min)', padding: '0 12px', width: '100%', boxSizing: 'border-box',
          border: '1px solid var(--h-border)', borderRadius: 'var(--h-radius-sm)', font: 'inherit',
        }}
      />
      {error && <p role="alert" style={{ margin: 0, color: 'var(--h-crit)' }}>{error}</p>}
      <PrimaryButton onClick={submit} disabled={!token || busy} busy={busy}>
        Создать passkey
      </PrimaryButton>
    </Screen>
  )
}

function PasskeyLoginScreen() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const optionsResponse = await fetch('/auth/passkey/login/options', {
        method: 'POST',
        credentials: 'same-origin',
      })
      if (!optionsResponse.ok) throw new Error('Не удалось начать вход')
      const options = (await optionsResponse.json()) as {
        challenge: string
        rp_id: string
        timeout_ms: number
        allow_credentials: { id: string; type: 'public-key' }[]
      }

      const assertion = (await navigator.credentials.get({
        publicKey: {
          challenge: fromBase64Url(options.challenge),
          rpId: options.rp_id,
          timeout: options.timeout_ms,
          userVerification: 'required',
          allowCredentials: options.allow_credentials.map((credential) => ({
            id: fromBase64Url(credential.id),
            type: 'public-key' as const,
          })),
        },
      })) as PublicKeyCredential | null
      if (!assertion) throw new Error('Вход отменён')

      const response = assertion.response as AuthenticatorAssertionResponse
      const verify = await fetch('/auth/passkey/login/verify', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          credential_id: toBase64Url(assertion.rawId),
          client_data: toBase64Url(response.clientDataJSON),
          authenticator_data: toBase64Url(response.authenticatorData),
          signature: toBase64Url(response.signature),
        }),
      })
      if (!verify.ok) throw new Error('Passkey не подтверждён')
      window.location.href = '/'
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось войти')
      setBusy(false)
    }
  }

  return (
    <Screen title="Вход">
      <p style={{ margin: 0, color: 'var(--h-mut)' }}>Telegram подтверждён. Подтвердите passkey.</p>
      {error && <p role="alert" style={{ margin: 0, color: 'var(--h-crit)' }}>{error}</p>}
      <PrimaryButton onClick={submit} disabled={busy} busy={busy}>
        Войти с passkey
      </PrimaryButton>
    </Screen>
  )
}

function Screen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div
      style={{
        maxWidth: 360, margin: '15vh auto 0', padding: '0 16px',
        display: 'flex', flexDirection: 'column', gap: 14,
      }}
    >
      <h1 style={{ margin: 0, fontSize: 'var(--h-fs-hero)' }}>{title}</h1>
      {children}
    </div>
  )
}
