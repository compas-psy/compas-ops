/* Проверка панели на запреты брифа §2 и §5.
 *
 * Статический анализ исходников и собранного бандла. Смысл в том, чтобы
 * нарушение ловилось на сборке, а не на визуальной приёмке через неделю:
 * «декоративная иконка при каждом числе» легко приезжает обратно с любой
 * правкой, и заметить её глазами на пятом экране уже некому.
 */

import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'

const SRC = 'src'
const DIST = 'dist'
let failures = []
let checks = 0

function check(name, condition, detail = '') {
  checks++
  if (!condition) failures.push(`${name}${detail ? ` — ${detail}` : ''}`)
}

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    return entry.isDirectory() ? walk(path) : [path]
  })
}

const sources = walk(SRC).filter((f) => /\.(ts|tsx|css)$/.test(f))
const allSource = sources.map((f) => readFileSync(f, 'utf8')).join('\n')
// Код без комментариев: запрет, описанный в комментарии, — не нарушение.
const code = allSource
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

// ── бриф §2: запрещённый AI-шум ─────────────────────────────────────────────

check('нет чат-виджета', !/chat|чат-виджет|спросить ассистент/i.test(code))
check('нет приветствий', !/доброе утро|добрый вечер|привет,|здравствуй/i.test(code))
check('нет AI-инсайтов', !/insight|инсайт|рекоменд(ует|ация)|резюме от/i.test(code))
check('нет конфетти и празднований', !/confetti|конфетти|celebrat/i.test(code))
check('нет skeleton-анимаций', !/skeleton|shimmer|pulse/i.test(code))
check('нет круговых индикаторов', !/gauge|donut|radial|progress-ring|circumference/i.test(code))
// U+2713 ✓ и U+2715 ✕ разрешены: бриф §3.2 сам записывает предусловия как
// «CI зелёный на a1b2c3d ✓» и прямо называет этот чеклист недекоративным.
// Запрет §2 касается эмодзи и декоративных иконок, а не этой нотации.
const BRIEF_MARKS = /[\u2713\u2715]/gu
check('нет эмодзи в интерфейсе',
  !/[\u{1F300}-\u{1FAFF}\u{FE0F}\u{2600}-\u{27BF}]/u.test(code.replace(BRIEF_MARKS, '')))
check('галочки только в списке предусловий',
  (readFileSync(join(SRC, 'sections/ApprovalCard.tsx'), 'utf8').match(BRIEF_MARKS) || []).length
    === (allSource.match(BRIEF_MARKS) || []).length)

// ── бриф §2: анимации только на смену состояния ─────────────────────────────

const transitions = [...code.matchAll(/transition[^;'"`]*?(\d+)ms/g)].map((m) => Number(m[1]))
check('длительность анимаций 150–200 ms', transitions.every((ms) => ms <= 200),
  `найдено: ${transitions.join(', ') || 'нет'}`)
check('нет анимации появления страницы',
  !/animation:\s*(?!none)|@keyframes/i.test(code))

// ── бриф §5: типографика и палитра ──────────────────────────────────────────

const tokens = readFileSync(join(SRC, 'styles/tokens.css'), 'utf8')
const fontSizes = new Set([...tokens.matchAll(/--h-fs-[a-z]+:\s*(\d+)px/g)].map((m) => m[1]))
check('ровно 4 размера шрифта', fontSizes.size === 4, `найдено ${fontSizes.size}: ${[...fontSizes]}`)

const weights = new Set([...tokens.matchAll(/--h-fw-[a-z]+:\s*(\d+)/g)].map((m) => m[1]))
check('ровно 2 начертания', weights.size === 2, `найдено ${weights.size}: ${[...weights]}`)

check('тёмная тема по системной настройке', /prefers-color-scheme:\s*dark/.test(tokens))
check('нет переключателя темы в UI', !/toggleTheme|theme-switch|переключить тему/i.test(code))
check('нет градиентов', !/linear-gradient|radial-gradient/i.test(code))
check('accent не Telegram-синий',
  !/#(229ED9|2AABEE|0088cc)/i.test(tokens))
check('строки не ниже 40 px', /--h-row-min:\s*4[0-4]px/.test(tokens))

// ── бриф §2: проценты только с абсолютом ────────────────────────────────────
// MetricRow требует value обязательным параметром — процент без абсолюта
// нельзя отрисовать, не передав вторую величину.
const primitives = readFileSync(join(SRC, 'components/primitives.tsx'), 'utf8')
check('MetricRow требует абсолютное значение',
  /value:\s*string(?!\s*\|)/.test(primitives))

// Найдено на визуальной приёмке: «Диск 41 %» вместо «41 / 100 GB».
// Статическая проверка не ловила это, потому что MetricRow получал строку —
// формально валидную. Ловим саму форму «значение + %» без второй величины.
const barePercent = [...code.matchAll(/`\$\{[^}]+\}\s*%`/g)].map((m) => m[0])
check('нет процента без абсолютной величины', barePercent.length === 0,
  barePercent.join(', '))

// ── §30.7: никаких mock-данных в продакшене ─────────────────────────────────

check('нет mock-данных в исходниках',
  !/\b(mockData|fixtures?|dummyData|sampleData|placeholderData|FAKE_)\b/i.test(code))
check('нет захардкоженных сумм-примеров', !/\$2[.,]40|\$3[.,]10/.test(code))
check('нет строки Telegram · webhook',
  !/webhook/i.test(code) || !/Telegram\s*·\s*webhook/i.test(code))

// ── §10.5.2: панель не зовёт модель ─────────────────────────────────────────

check('нет обращений к моделям',
  !/openai|litellm|openrouter|anthropic\.com|api\.telegram/i.test(code))

// ── собранный бандл ─────────────────────────────────────────────────────────

if (existsSync(DIST)) {
  const bundle = walk(DIST).filter((f) => f.endsWith('.js'))
    .map((f) => readFileSync(f, 'utf8')).join('\n')
  check('в бандле нет sourcemap-ссылки', !/sourceMappingURL/.test(bundle))
  check('в бандле нет mock-значений', !/\$2[.,]40|Иван Иванов|Lorem ipsum/i.test(bundle))
  const html = readFileSync(join(DIST, 'index.html'), 'utf8')
  check('viewport-fit=cover для safe-area', /viewport-fit=cover/.test(html))
  check('страница закрыта от индексации', /noindex/.test(html))
}

// ── итог ────────────────────────────────────────────────────────────────────

if (failures.length) {
  console.error(`✕ Нарушений брифа: ${failures.length} из ${checks} проверок\n`)
  for (const failure of failures) console.error(`  · ${failure}`)
  process.exit(1)
}
console.log(`✓ Бриф соблюдён: ${checks} проверок пройдено`)
