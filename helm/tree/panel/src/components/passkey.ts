/* Passkey step-up (ТЗ §10.5.8, §10.5.8.1).
 *
 * Церемония запрашивается ПОД конкретное действие: сервер выдаёт challenge,
 * привязанный к approval_id и его action_hash, живущий 60 секунд. Полученный
 * идентификатор одноразовый — повторно использовать его нельзя, и клиент не
 * хранит его нигде. Никакого «запомнить на 30 дней» (§10.5.8).
 */

import { fromBase64Url, toBase64Url } from '../api/codec'

export class PasskeyCancelled extends Error {}

interface ChallengeResponse {
  challenge_id: string
  challenge: string
  rp_id: string
  allow_credentials: { id: string; type: 'public-key' }[]
  timeout_ms: number
}

/**
 * Провести церемонию для одного действия и вернуть идентификатор
 * подтверждения для заголовка X-Helm-StepUp.
 */
export async function stepUpFor(approvalId: string, actionHash: string): Promise<string> {
  return ceremony({ approval_ids: [approvalId], action_hashes: [actionHash] })
}

/**
 * Церемония для операции БЕЗ одобрения — раздел «Пользователи» (v3.8
 * §14.3, P8.6.5). `approval_ids` пуст намеренно: такое подтверждение
 * физически не может одобрить действие (сервер ищет approval_id в этом
 * списке), а `scope` привязывает его к конкретной операции над
 * конкретным пользователем, как action_hash привязывает к одобрению.
 */
export async function stepUpForScope(scope: string): Promise<string> {
  return ceremony({ approval_ids: [], action_hashes: [scope] })
}

async function ceremony(binding: { approval_ids: string[]; action_hashes: string[] }): Promise<string> {
  if (!window.PublicKeyCredential) {
    throw new Error('Браузер не поддерживает passkey — вход невозможен')
  }

  const optionsResponse = await fetch('/auth/passkey/assert/options', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(binding),
  })
  if (!optionsResponse.ok) throw new Error('Не удалось начать подтверждение')
  const options = (await optionsResponse.json()) as ChallengeResponse

  let assertion: PublicKeyCredential | null
  try {
    assertion = (await navigator.credentials.get({
      publicKey: {
        challenge: fromBase64Url(options.challenge),
        rpId: options.rp_id,
        timeout: options.timeout_ms,
        // §10.5.7: userVerification обязателен — иначе passkey перестаёт быть
        // вторым фактором и становится просто наличием устройства.
        userVerification: 'required',
        allowCredentials: options.allow_credentials.map((credential) => ({
          id: fromBase64Url(credential.id),
          type: 'public-key' as const,
        })),
      },
    })) as PublicKeyCredential | null
  } catch (cause) {
    throw new PasskeyCancelled('Подтверждение отменено')
  }
  if (!assertion) throw new PasskeyCancelled('Подтверждение отменено')

  const response = assertion.response as AuthenticatorAssertionResponse
  const verify = await fetch('/auth/passkey/assert/verify', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      challenge_id: options.challenge_id,
      credential_id: toBase64Url(assertion.rawId),
      client_data: toBase64Url(response.clientDataJSON),
      authenticator_data: toBase64Url(response.authenticatorData),
      signature: toBase64Url(response.signature),
    }),
  })
  if (!verify.ok) throw new Error('Подтверждение не принято')
  return options.challenge_id
}
