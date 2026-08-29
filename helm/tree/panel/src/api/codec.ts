/* base64url ↔ ArrayBuffer. Общее для passkey.ts (step-up) и Login.tsx (вход/enrollment) —
 * обе стороны говорят с Control Plane одним и тем же кодированием WebAuthn-полей. */

export function fromBase64Url(value: string): Uint8Array {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(padded.padEnd(padded.length + ((4 - (padded.length % 4)) % 4), '='))
  return Uint8Array.from(binary, (char) => char.charCodeAt(0))
}

export function toBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
