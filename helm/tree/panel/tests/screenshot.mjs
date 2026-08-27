/* Визуальная приёмка §10.5.11 — три обязательных вьюпорта.
 *
 * API подменяется на уровне сети (route interception), а НЕ внутри панели:
 * §30.7 требует, чтобы в продакшене не осталось mock-данных. Заглушка живёт
 * в тесте и в бандл не попадает — это принципиально разные вещи.
 */

import { chromium } from 'playwright'
import { createServer } from 'node:http'
import { readFileSync, existsSync, mkdirSync } from 'node:fs'
import { join, extname } from 'node:path'

const DIST = 'dist'
const OUT = 'tests/screenshots'
const PORT = 4173

const VIEWPORTS = [
  { name: '390x844-mobile', width: 390, height: 844 },
  { name: '430x932-mobile-large', width: 430, height: 932 },
  { name: '1440x900-desktop', width: 1440, height: 900 },
]

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }

const server = createServer((req, res) => {
  const path = req.url === '/' ? '/index.html' : (req.url ?? '/').split('?')[0]
  const file = join(DIST, path)
  if (!existsSync(file)) {
    res.writeHead(200, { 'Content-Type': 'text/html' })
    return res.end(readFileSync(join(DIST, 'index.html')))
  }
  res.writeHead(200, { 'Content-Type': MIME[extname(file)] ?? 'application/octet-stream' })
  res.end(readFileSync(file))
})

// Два состояния из брифа §6, которые обязаны быть спроектированы.
const STATES = {
  calm: {
    label: 'всё в порядке, 0 одобрений',
    today: {
      generated_at: new Date().toISOString(),
      approvals: { count: 0, items: [] },
      money: { spent_today_usd: '2.40', hard_limit_usd: '10.00', kill_switch_active: false },
      tasks: { running: 3, stuck: [] },
      overnight: [
        { id: 'a1', title: 'Ночная сводка по СИМПАС', finished_at: iso(-7) },
        { id: 'a2', title: 'Проверка зеркала Forgejo', finished_at: iso(-6) },
      ],
    },
    guardian: { status: 'ok', generated_at: new Date().toISOString(), degraded: false },
  },
  monday: {
    label: '1 критический алерт + 5 одобрений',
    today: {
      generated_at: new Date().toISOString(),
      approvals: {
        count: 5,
        items: [
          brief('b1', 'Публикация в публичный канал', 'publish_public_content', 'publication', 4),
          brief('b2', 'Слить в main', 'merge_main', 'git_merge', 11),
          brief('b3', 'Потратить деньги', 'spend_money', 'spend', 1),
        ],
      },
      money: { spent_today_usd: '8.90', hard_limit_usd: '10.00', kill_switch_active: false },
      tasks: {
        running: 4,
        stuck: [{ id: 't9', title: 'Разбор витрины Metabase', stuck_since: iso(-3) }],
      },
      overnight: [{ id: 'a3', title: 'Сборка деки борда', finished_at: iso(-9) }],
    },
    guardian: { status: 'critical', generated_at: new Date().toISOString(), degraded: true },
  },
}

function iso(hours) { return new Date(Date.now() + hours * 3600_000).toISOString() }
function brief(id, title, type, view, dueHours) {
  return {
    id, short_id: id, action_type: type, title_ru: title, panel_view: view,
    expires_at: iso(dueHours), requested_at: iso(-2),
    action_hash_short: 'a1b2c3d4e5f6', task_id: 't1',
  }
}

const SYSTEM = {
  resources: [
    { metric: 'disk', value: '41', at: iso(0), labels: { detail: '41 / 100 GB' } },
    { metric: 'ram', value: '7.1', at: iso(0), labels: { detail: '7,1 / 12 GB' } },
    { metric: 'backup_age', value: '6', at: iso(0), labels: null },
  ],
  routines: [
    { id: 'r1', name: 'Утренняя сводка', schedule: '07:30', enabled: true,
      last_run_at: iso(-5), last_status: 'ok', consecutive_failures: 0 },
  ],
}

await new Promise((resolve) => server.listen(PORT, resolve))
mkdirSync(OUT, { recursive: true })

// Среда несёт предустановленный Chromium (сборка 1194); playwright из
// package.json ждёт другую. Берём предустановленный вместо докачивания —
// PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD в этой среде выставлен намеренно.
const EXECUTABLE = process.env.HELM_CHROMIUM
  ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'
const browser = await chromium.launch(
  existsSync(EXECUTABLE) ? { executablePath: EXECUTABLE } : {})
const results = []

for (const [key, state] of Object.entries(STATES)) {
  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: 2,
      colorScheme: key === 'monday' ? 'dark' : 'light',
    })
    const page = await context.newPage()

    await page.route('**/api/panel/v1/**', (route) => {
      const url = route.request().url()
      const body = url.includes('/today') ? state.today
        : url.includes('/approvals') ? { items: state.today.approvals.items }
        : url.includes('/system') ? SYSTEM
        : {}
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: JSON.stringify(body) })
    })
    await page.route('**/guardian/status.json', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json',
                      body: JSON.stringify(state.guardian) }))

    const errors = []
    page.on('pageerror', (e) => errors.push(String(e)))
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

    await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(250)

    const file = join(OUT, `${key}-${viewport.name}.png`)
    await page.screenshot({ path: file })

    // §10.5.11: на мобильном нет горизонтальной прокрутки.
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth)
    // §10.5.11: Today в спокойном состоянии умещается без скролла на 390×844.
    const verticalOverflow = await page.evaluate(() =>
      document.documentElement.scrollHeight - window.innerHeight)

    results.push({ state: key, viewport: viewport.name, file,
                   horizontalOverflow: overflow, verticalOverflow, errors })
    await context.close()
  }
}

await browser.close()
server.close()

let failed = false
console.log('\nВизуальная приёмка §10.5.11\n')
for (const r of results) {
  const hOk = r.horizontalOverflow <= 0
  const noErrors = r.errors.length === 0
  const calmFits = !(r.state === 'calm' && r.viewport === '390x844-mobile' && r.verticalOverflow > 0)
  const ok = hOk && noErrors && calmFits
  if (!ok) failed = true
  console.log(`${ok ? '✓' : '✕'} ${r.state} · ${r.viewport}`)
  if (!hOk) console.log(`    горизонтальная прокрутка: +${r.horizontalOverflow}px`)
  if (!calmFits) console.log(`    Today не умещается на 390×844: +${r.verticalOverflow}px`)
  if (!noErrors) console.log(`    ошибки: ${r.errors.slice(0, 2).join(' | ')}`)
}
process.exit(failed ? 1 : 0)
