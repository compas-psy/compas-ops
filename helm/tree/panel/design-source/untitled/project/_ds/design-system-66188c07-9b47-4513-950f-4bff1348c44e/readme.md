# СИМПАС — Design System

**СИМПАС** (COMPAS) is a calm working environment for helping specialists — psychologists, coaches, and career counselors running a private practice. It is deliberately **not a CRM**: a single quiet place to run the whole practice — schedule, client cards, session notes, reminders, and client self-booking — across a web cabinet (cmpas.ru) and a Telegram / MAX mini-app.

The brand feeling is grounded and trustworthy: deep forest green as the base of trust, a warm gold accent for the few premium moments, and a milky-cream ground that's easy on the eyes. The physical brand diary reads *«Здесь можно быть собой»* — "here you can be yourself." That warmth, restraint, and respect for privacy drive every design decision.

## Products represented
- **Marketing site** (`cmpas.ru`) — landing that positions СИМПАС as "a calm space for practice, not a CRM."
- **Кабинет / Diary** (`/diary`) — the psychologist's workspace: today's dashboard, schedule, client cards, structured session notes (private vs. client-facing).
- **Auth & onboarding** — Yandex OAuth + email sign-in for specialists.
- **Client flow** (Telegram/MAX mini-app) — clients self-book and get reminders.

## Sources
Built from the private repository **`compas-psy/cmpas.ru`** (Next.js 16 / React 19 / Tailwind CSS 4 / shadcn-Radix). Explore it to build richer, more accurate designs:
- Repo: https://github.com/compas-psy/cmpas.ru
- Related repos (not used here): `compas-psy/compas-voice` (Android voice-practices app), `compas-psy/ilyamartynov.ru`.
- Key files referenced: `src/app/globals.css` (token source of truth), `DESIGN_SYSTEM_SPEC.md`, `DESIGN_QUICK_REFERENCE.md`, `src/components/landing/*`, `src/app/diary/page.tsx`, `src/app/auth/page.tsx`, `src/components/icons/note-icons.tsx`, `src/components/ui/*`.

Note: the older `DESIGN_SYSTEM_SPEC.md` lists an earlier palette (`#1a4d3a` / `#c9a961`). The **live `globals.css` values win** and are what this system ships (forest `#1D4735`, gold `#CC9E50`, cream `#F7F8F4`).

---

## CONTENT FUNDAMENTALS

- **Language:** Russian, formal-polite **«вы»** (never «ты»). The product speaks *to* the specialist, warmly and as an equal.
- **Tone:** calm, grounded, reassuring. Short declarative promises, often in threes: *«Порядок в практике. Время на главное. Рост без хаоса.»* Sentences are plain and concrete, never salesy.
- **Casing:** Sentence case for body and headings. The wordmark **СИМПАС** is set in all-caps with wide tracking (`letter-spacing: 0.12em`). Small section labels use uppercase captions (`Следующая сессия`, `На сегодня`) at 10–11px, bold, letter-spaced.
- **Vocabulary — say:** «спокойное рабочее пространство», «клиенты сами записываются», «данные под контролем», «заметки после сессии», «карточка клиента».
- **Vocabulary — avoid:** CRM jargon (воронки, лиды, конверсия), hype, and growth-hacking language. **No gamification** (no streaks, badges, points) — a stated product principle.
- **Punctuation:** no exclamation marks in product surfaces; uses non-breaking spaces (`&nbsp;`) to keep short prepositions attached. Prices as `990&nbsp;₽`.
- **Emoji:** essentially none in the marketing/product chrome. The dashboard greeting shows a single friendly `👋`, and structured-note summaries use small category emoji (📈 👁 🛠 📝 ➡️ 🎯) as compact labels — but UI you build should default to the **custom stroke Icon set**, not emoji.
- **Vibe:** a quiet, well-kept notebook. Professional but human; privacy-first ("the client never sees the specialist's hypotheses").

---

## VISUAL FOUNDATIONS

**Colors.** Deep **forest green** (`#1D4735`, primary — sidebar, buttons, deep surfaces) is the base of trust. **Gold** (`#CC9E50`, accent) is used sparingly for the single most important / premium action. **Sage** tints (`#F6FAF6`–`#DDE9E1`) fill soft surfaces, avatar circles, and chips. The ground is **milky cream** (`#F7F8F4`), never pure white behind cards. Text is near-black forest **ink** (`#142018`) with a muted `#5F6C64` for secondary. Status uses **soft-tint pairs** (soft bg + saturated fg): green=confirmed, orange=pending, red=cancelled, blue=processing, violet=notes, gold=new. Max 1–2 background colors per view (cream + forest).

**Type.** **Geist** (sans) for everything UI; **Geist Mono** for URLs/links, times, and tabular numbers. Weights 400/500/600/700 — headings 700 with tight tracking (`-0.02em`); labels/buttons 600; body 450–500. Dashboard H1 ~36–40px; section titles 20px; body 14–15px; captions 10–11px uppercase. KPI numbers 32px with `tabular-nums`.

**Spacing & layout.** 4px base (Tailwind scale). Content max-width `1240px`. Dashboard is a `1fr` sidebar + content grid; panels use a 2/3 + 1/3 split (timeline + right rail). Generous padding inside cards (16–20px). Mobile-first, iOS patterns.

**Backgrounds.** Mostly flat cream. One signature **forest gradient band** for emphasis sections: `radial-gradient(circle at 80% 20%, rgba(204,158,80,.16), transparent 34%), linear-gradient(135deg,#143D2F,#1D4735 55%,#285B46)` — deep green with a faint warm gold glow. Real photography is **warm, natural, green-and-cream** (leather diaries, plants, soft daylight) — never cold or corporate stock. No busy patterns or textures in UI.

**Corner radii.** Everything is softly rounded (iOS): inputs/small cards 12px, buttons 14–16px, dashboard cards & modals 20–22px, large panels 24px, chips/avatars/toggles fully pill. Modals on mobile are bottom sheets rounded only on top (`16px 16px 0 0`).

**Cards.** White surface, 1px `#E4E9E3` hairline border, generous rounding (20–22px), and a **soft, low, green-tinted shadow** (`0 8px 30px rgba(20,32,24,.06)`). Never harsh drop shadows. Hover lifts the shadow slightly. Emphasis cards on the forest band use `bg-white/8` + `backdrop-blur`.

**Buttons.** Primary = solid forest, white text, hover → `forest-700`. Accent = solid gold (forest or white text) for the premium action. Secondary = white/transparent with hairline border, hover → `sage-50`. All share ~14px radius, `shadow-sm`, and the **iOS press feel: `active:scale(0.97)`**.

**States.** Hover = subtle background tint (`sage-50`/`sage-100`) or one shade darker for solid buttons — never opacity dimming for primary actions. Press = `scale(0.97)` (haptic-light). Focus = 3px **gold** ring (`--ring` at ~45% alpha). Disabled = `opacity: .5` + `not-allowed`.

**Borders & dividers.** Single hairline `#E4E9E3`. List rows separated by `divide-border/50` (even lighter). Active/selected note cards get a `1.5px forest` border instead of the default.

**Motion.** Calm and quick — `150–300ms ease`. Entrances fade + slide-from-bottom (`animate-in`). No bounces, no springy overshoot, no parallax. Live "now" indicators use a gentle pulsing dot.

**Transparency & blur.** Sticky header is cream at ~82% with `backdrop-blur(18px)`. Cards over the forest band use white at 8–10% with blur. Otherwise surfaces are opaque.

**Imagery vibe.** Warm, calm, natural light; forest green + cream + gold; real objects (leather notebooks, coffee, plants). No grain, no B&W, no cold blue tech imagery.

---

## ICONOGRAPHY

- **Two icon sources.** (1) **Lucide** (`lucide-react`, stroke 1.5–1.75) for generic UI glyphs throughout the app — link Lucide from CDN in consuming pages. (2) A **custom stroke icon set** in `src/components/icons/note-icons.tsx` for note blocks and toolbar actions, ported here verbatim as the **`Icon`** component. Both share the same visual language: monochrome, `currentColor`, 24 viewBox, **strokeWidth 1.75**, round caps/joins.
- **Custom `Icon` names.** Note blocks: `request, anamnesis, observation, intervention, resources, dynamics, homework, next_step, private, for_client, quote, hypothesis`. Actions: `search, add, filter, import, export, share, archive, delete, save, close, back, mic, attach, tags, more, sync, meeting, payment, format, list`.
- **Emoji as icons:** only the greeting `👋` and compact category labels in note summaries. Do not introduce new emoji.
- **Logo / brand mark:** a **gold/green stylized tree** (growth, rootedness). Shipped in `assets/` as `logo.svg` (509×509, white tree on forest disc), `logo-mark.png`, `logo-full.png` (mark + wordmark), `logo-tree.png`, `logo-footer.png`. Third-party marks present in the product: `yandex-logo.png` (Yandex OAuth). Always pair the mark with the all-caps wide-tracked **СИМПАС** wordmark when space allows.
- **Never** hand-draw a new brand mark or invent icons outside these two systems.

---

## Index / Manifest

**Root**
- `styles.css` — global entry (import this). `@import`s everything below.
- `tokens/` — `fonts.css`, `colors.css`, `typography.css`, `radius.css`, `shadows.css`, `services.css`, `base.css`.
- `assets/` — `logo.svg`, `logo-mark.png`, `logo-full.png`, `logo-tree.png`, `logo-footer.png`, `yandex-logo.png`.
- `public/images/` — brand photography (`hero.jpg`, `auth-side.jpg`, `main_screen_photo_v2.jpg`), `public/forest.jpg`.
- `SKILL.md` — Agent-Skills manifest for downloadable use.

**Components** (`window.DesignSystem_66188c.<Name>`)
- `components/core/` — **Button**, **Badge**, **Card** (+ `CardHeader`, `CardBody`), **Input**, **Separator**
- `components/navigation/` — **SegmentedControl**
- `components/icons/` — **Icon** (custom brand stroke set; `ICON_NAMES`)
- `components/brand/` — **ServiceMark** (+ `SERVICES`): знак сервиса — одно дерево, девять цветовых пар

**Guidelines / specimen cards** (`guidelines/`) — Colors (forest, sage, accent+neutrals, status), Type (scale, families), Spacing (radii, elevation, spacing), Brand (logo, voice & tone, service colors, Android adaptive icons).

**Иконки приложений** — `assets/services/<сервис>.svg` (квадратная плитка для iOS/веба) и `assets/services/android/` (adaptive icon: слои `-bg`, `-fg`, `-mono` на канве 108 dp, дерево в 62 dp безопасной зоны; правила и сборка — `assets/services/android/README.md`).

**Handoff** — `HANDOFF.md` (полная текстовая передача: состав, подключение, токены, API компонентов, правила, чеклист приёмки) и `guidelines/handoff-sheet.html` (лист графических материалов).

**Брифы для продуктовых команд** (`guidelines/`) — `zapiski-handoff.md`: что забрать из системы и что учесть в ТЗ проекта ЗАПИСКИ. Шаблон для остальных сервисов.

**UI kits** (`ui_kits/`)
- `ui_kits/landing/` — marketing site recreation (hero, features, positioning band, pricing).
- `ui_kits/diary/` — the psychologist dashboard (sidebar, greeting, week strip, next session, today's schedule, right rail).
- `ui_kits/auth/` — sign-in screen.

### Знаки сервисов
Бренд переименован из КОМПАС в **СИМПАС** (под домен cmpas.ru). Знак — дерево — не менялся; изменилось только название.

Все сервисы используют **тот же самый знак**; различает их только пара «фон + дерево»:

| Сервис | Что это | Фон | Дерево |
| --- | --- | --- | --- |
| ПРАКТИКА | кабинет специалиста (мастер) | `#1D4735` форест | `#F7F8F4` |
| МОМЕНТЫ | медитации и практики | `#4A4E78` Future Dusk | `#EDEBF2` |
| ЗАПИСКИ | заметки | `#C8604A` терракота | `#FBF3E3` |
| ШАГИ | задачи | `#3B8F5A` светлый форест | `#F7F8F4` |
| СТУПЕНИ | повышение квалификации | `#CC9E50` золото | `#143D2F` |
| ГРАНИ | диагностика, психоанкетирование | `#5C2F42` слива | `#F5EAEF` |
| КРУГИ | супервизия, интервизия, балинты | `#6A5F63` Cinnamon Slate | `#F7F3F1` |
| СВОИ | сообщество | `#A47864` Mocha Mousse | `#FAF5F0` |
| ДНЕВНИК | для клиентов | `#E7F0EA` сейдж | `#1D4735` |

**Взгляд психолога.** Аудитория — практикующие психологи, поэтому цветовые обоснования держатся только на воспроизводимом: возбуждение задаётся насыщенностью и светлотой, а не оттенком. Связки «оттенок → эмоция» называются интерпретацией, а не фактом; цветовые типологии личности (Люшер и т.п.) не используются вообще, и цвет никогда не выступает диагностическим признаком внутри продуктов. Вторым каналом идёт фонетика имени (эффект «буба–кики», величинная символика гласных). Разбор по каждому сервису — в `Simpas Service Marks.html`, раздел 05; два расхождения (ЗАПИСКИ звучат тише, чем выглядят; ГРАНИ — острее) оставлены осознанно.

**Зелёный закреплён за осью практики** — ПРАКТИКА (сам кабинет) и ШАГИ (ежедневное действие в нём); они различаются светлотой и это единственная пара родственных тонов. Остальные сервисы из зелёного уходят, иначе мастер-цвет перестаёт что-либо значить. Три тона (Future Dusk, Cinnamon Slate, Mocha Mousse) взяты из трендовой карты 2025–26 и приглушены: тренд работает на периферии, ядро палитры остаётся вне моды.

**Имя продукта пишется без приставки** — «ЗАПИСКИ», не «СИМПАС Записки». СИМПАС появляется только там, где важен владелец: договоры, футер, счета, письма поддержки.

Файлы: токены `tokens/services.css`, иконки `assets/services/*.svg`, компонент `ServiceMark`, разбор системы — `Simpas Service Marks.html`.

### Intentional additions
- **Icon** — the source defines its custom glyphs as loose exports in `note-icons.tsx`; wrapping them in one named `Icon` component (name-driven) gives consumers a clean, discoverable API. Faithful port, no new glyphs.
- **SegmentedControl** and **Input** are promoted from repeated inline patterns / `globals.css` component classes (`.note-segmented`, auth field styles) into reusable primitives.

### Caveats
- The app itself ships **system fonts** (`-apple-system…`) for a native iOS feel; this system pins **Geist** (the family declared in `layout.tsx` via `next/font`) so recreations render consistently. Geist is loaded from Google Fonts — swap for self-hosted `.woff2` for offline use.
- shadcn/Radix primitives in the repo (dialog, dropdown, calendar, table, tooltip…) are **not** re-implemented here — only the families СИМПАС actually customizes. Ask if you need more.
