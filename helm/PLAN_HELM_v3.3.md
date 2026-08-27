# План реализации HELM v3.3

Источник: `HELM_FINAL_v3.3_2026-08-27.md` (4161 строка) + `START_HELM_IMPLEMENTATION_AGENT_v3.3.md`.
Составлен 27.08.2026. Метод — ultraplan. Статус реализации: **не начата, блокирована на входе**.

---

## 1. Что проверено фактически

| Проверка | Доказательство | Результат |
|---|---|---|
| SHA256 handoff-бандла | `sha256sum` = `84b1184b…d52b19` | **совпадает** с §6.1 |
| Состав handoff | 17 файлов, `untitled/project/HELM Panel.dc.html` (135 КБ) + `_ds/` + `screens/01-today.png` | на месте |
| `HELM_PANEL_DESIGN_BRIEF.md` | нет ни в архиве, ни во вложениях | **отсутствует** |
| DNS | `helm.cmpas.ru` → 185.250.44.137, `git.cmpas.ru` → 185.250.44.137 | готов |
| SSH до VPS (:22) | `nc -vz` → timeout, rc=124 | **недоступен** |
| HTTPS до VPS (:443) | egress-прокси: `403 CONNECT — policy denial`, host `185.250.44.137:443` | **запрещён политикой** |
| SSH-клиент в среде | `/usr/bin/ssh*` отсутствует | не установлен |
| Альтернативные среды | обе — `anthropic_cloud`, та же политика egress | не помогут |
| Секреты на входе | только HOST + root-пароль | OpenRouter / Telegram / backup **отсутствуют** |
| Граф знаний (CLAUDE.md §5.7) | нет каталога `graph/`, `graphify` не установлен | работа по файлам, находка в отчёт |

**Вывод.** Эта сессия не может исполнять P0–P15. Спека это предусматривает: §31.0 запрещает подменять
установку патчем/репозиторием **«если SSH доступ работает»**. Он не работает и не может быть открыт изнутри
(README прокси: «не обходить, не ретраить, сообщить о заблокированном хосте»). Легальный путь — §31.0
абзац про ограниченную среду: зафиксировать ограничение в `PRE-FLIGHT.md`, построить офлайн всё, что не
требует сервера, и исполнить живые фазы из среды с доступом.

---

## 2. Блокеры входа

Ни один не снимается агентом. Формат — `charter/04_TEMPLATES §4`, устав §6.2.

| ID | Что | Что блокирует | Как снять |
|---|---|---|---|
| **B1** | Среда с рабочим SSH до 185.250.44.137 | **всё** (P0–P15) | локальный Claude Code на машине владельца, либо любой хост с egress на :22 |
| **B2** | `OPENROUTER_API_KEY` | P3 → жёсткий гейт → P4, P5, весь Milestone A | положить в `/root/helm-bootstrap/openrouter_api_key`, `chmod 600` |
| **B3** | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_OWNER_ID` | P4, A-DoD п.1–4 | там же |
| **B4** | backup credentials (S3/restic) | P5, A-DoD п.10 (restore test) | там же |
| **B5** | `HELM_PANEL_DESIGN_BRIEF.md` (SHA `5b0b375d…9f0a30`) | P7.5 приёмка §30.7 | прислать файл |
| **B6** | root-пароль пришёл открытым текстом в `.md` | безопасность | CLAUDE.md §5.4: считать скомпрометированным, ротировать на P1 |
| **B7** | Подтверждённый доступ к консоли хостера (VNC/rescue) | P1 — отключение password-login | проверить в панели хостера **до** P1 |
| **B8** | BotFather Web Login: `client_id` / `client_secret` / Allowed URLs | активация auth панели (§10.5.6, D10) | 10 мин владельца на P7.5; не блокирует сборку панели |

Не блокируют Milestone A и берутся позже: `github_token`, `max_bot_token`/`max_owner_id`,
`context7_api_key`, `seed/psy_CLAUDE.md` (P10), SignalAI credentials (P13).

---

## 3. Потолок одного прогона

P15 за один прогон **физически недостижим по гейтам самой спеки**:

- §2.4 + P12: Milestone D входит только после «A–C PASS + **7 дней baseline** + отсутствие resource pressure».
- D6: graduated trust — не раньше 10 supervised successes.

Честный DoD первого прогона: **P0 → P12 + P14 (частично)**. P13/P15 — второй прогон после недельного окна.
Обещать P15 «непрерывно» — значит либо нарушить гейт, либо соврать в отчёте.

---

## 4. Критический путь и параллелизм

```text
ЖИВОЙ СЕРВЕР (один писатель на путь, §31.0)
P1 Host → P2 Control Plane → P3 LiteLLM ⛔гейт → P4 Hermes → P5 Guardian ─┬─ ✅ Milestone A
                                                                          ├─ P6 n8n
                                                                          ├─ P6.5 Forgejo
                                                                          └─ P7 MAX+MCP → P7.5 Panel → P8 lane
ОФЛАЙН (изолированный work dir, параллельно с P2+)
  Panel frontend · Guardian scripts · policy YAML · миграции · тест-фикстуры · ADR · docs
РЕВЬЮЕР (другое семейство моделей)
  читает evidence каждой фазы; финальное чтение — P14
```

**Главный выигрыш от параллелизма.** Панель (P7.5) — самая длинная одиночная работа и она не требует
живого сервера. Замораживаем OpenAPI-контракт panel API в конце P2 → фронт строится офлайн параллельно
шести живым фазам. Экономия ≈ 4–6 сессий календарно.

---

## 5. Фазы

Модель-класс — по §31.0. Frontier только там, где указано; рутина уходит вниз, запись в `MODEL_USAGE.jsonl`.

| Фаза | Выход | Гейт (§30) | Класс модели | Сессий |
|---|---|---|---|---|
| **P0** Preflight | `docs/PRE-FLIGHT.md`: 16 источников §37 сверены с живыми доками; хеши панели; фиксация ограничений среды | без деструктива | Flash (выгрузка) + Frontier (противоречия→ADR) | 1 |
| **P1** Host | OS, admin+key, firewall 22/80/443, Docker, Caddy+TLS, TZ Helsinki, дерево §5, ротация логов, checkpoint-скрипт | reboot жив; **второй независимый login до отключения пароля**; ротация B6 | GLM/Pro | 1 |
| **P2** Control Plane | PostgreSQL+pgvector (версия пинуется в P0, §33), 4 БД + health-схема, 15 таблиц §7.2, append-only `task_events` на уровне роли, DBOS-спайк→ADR-012, internal+panel API, action registry (typed→canonical→SHA256→preconditions→idempotency→audit→unit-test→`title_ru`/`panel_view`), `actions.yaml`, approvals TTL 24ч/2ч, outbox, routines | §30.2 — 9 тестов | Sonnet-класс; спайк — Frontier | 3–5 |
| **P3** LiteLLM | БД, proxy на localhost, каталог, aliases, virtual keys, бюджеты, primary+fallback | **§30.4. Реальный completion через OpenRouter. Реальный fallback искусственным отказом. usage/cost. FAIL → стоп** | GLM/Pro | 1 |
| **P4** Hermes | пинованный stable, профили, **Telegram-gateway только у chief**, API на localhost, LiteLLM как custom endpoint, плагин `helm-control`, allowlist владельца, web search, skills, sync жизненного цикла с CP | §30.3 + A-DoD 1–4. Ключевое: **регистрация в CP до первого LLM-вызова**; `/helm_approve` не уходит в LLM | Sonnet-класс | 2 |
| **P5** Guardian+backup | systemd (не Docker), прямой аварийный алерт, restic, offsite, cleanup dry-run, форкаст, morning brief | §30.10 — жив при остановленных Docker/PG/CP/Hermes/n8n; cleanup не трогает named volumes; **restore test PASS** | GLM/Pro | 1–2 |
| — | **Milestone A acceptance** — §30.1–30.4 | 10 пунктов A-DoD | — | 1 |
| **P6** n8n | Community, своя БД, постоянный `N8N_ENCRYPTION_KEY`, editor только по SSH, точный OAuth callback, export/restore | §30.6; в экспорте нет значений credentials | Flash/GLM | 1 |
| **P6.5** Forgejo | `git.cmpas.ru`, private by default, `helm-infra`, push-mirror в GitHub, CI по точному SHA | §30.8; **зелёный старый SHA не принимается** — отдельный намеренный тест | GLM/Pro | 1–2 |
| **P7** MAX+MCP | webhook прямо в CP, HMAC, именованный диалог, Context7/GitHub/Claude Design/n8n-read | §30.5 — MAX жив при мёртвых Telegram и n8n | GLM/Pro | 1–2 |
| **P7.5** Panel | React+TS+Vite → static dist за Caddy; 5 разделов; Telegram OIDC+PKCE; WebAuthn enrollment/step-up/**привязка challenge к action_hash 60с**/recovery-скрипт; 3 вьюпорта | §30.7 — 18 тестов. GET не будит LLM. Ни одного mock-значения | Kimi/Sonnet-класс | 4–6 |
| **P8** Dev lane | task→branch→tests→reviewer→Forgejo→Actions по точному SHA→PR | сквозной прогон | GLM/Pro | 1 |
| **P9–P12** Домены | СИМПАС · психология · Venture · Health (изоляция!) | §30.9 cross-domain leak = 0 | GLM/Pro | 4 |
| — | **Milestone C baseline** | **7 дней метрик** | — | пауза |
| **P13** SignalAI | §24 M0–M10 | §30.11; один писатель; snapshot хостера до cutover | Sonnet+Frontier на cutover | 3–4 |
| **P14** Приёмка | полный §30 + **стороннее чтение моделью другого семейства** | §30.12 целиком | Reviewer другого семейства | 2 |
| **P15** Handoff | 17 документов §35 | без значений секретов | Flash + Frontier на сводку | 1 |

**Итого до Milestone C: ≈ 22–28 агенто-сессий.** Далее пауза 7 дней, затем P13–P15: ещё 6–7.

---

## 6. Реестр рисков

| # | Риск | Вероятность | Цена | Митигация |
|---|---|---|---|---|
| **R1** | Публичный hook/plugin API Hermes не даёт перехватить сообщение **до** первого LLM-вызова | средняя | A-DoD п.2 и п.3 недостижимы — ядро безопасности | Проверить в **P0** по докам hooks/telegram. Если нет — gateway-обёртка перед адаптером Telegram + ADR-013. Не патчить ядро |
| **R2** | DBOS-спайк FAIL | средняя | +1 сессия | Заложено: ADR-012 + простая Postgres state machine (§7.4). Не тащить третью платформу |
| **R3** | Web Login у бота не включён / Allowed URLs не заданы | высокая | auth панели не активируется | Строим фронт и backend полностью, отдаём владельцу **один** точный шаг BotFather (D10) |
| **R4** | Локаут на P1 при отключении password-login | низкая | потеря сервера | **B7 обязателен до P1.** Порядок §6.6 строго: admin → key → *второй независимый login* → только потом отключение |
| **R5** | Нет design brief (B5) | **факт** | §10.5.1 теряет верхний авторитет поведения; прототип по спеке **не имеет права** определять поведение | Либо прислать файл, либо ADR: поведение из §10.5.4–10.5.10, прототип — только визуал |
| **R6** | 12 ГБ на PG+Hermes+LiteLLM+n8n+Forgejo+Caddy | средняя | деградация | §4.1 запрещает превентивный апгрейд. Guardian (P5) обязан работать **до** P6/P6.5. Решение — только по метрикам (D5) |
| **R7** | Дрейф каталога/цен OpenRouter относительно снимка 27.08.2026 | высокая | неверные model ID | P0 сверяет каталог живьём; §15.6 — кандидаты, не догма |
| **R8** | Root-пароль скомпрометирован доставкой (B6) | **факт** | доступ к серверу | Ротация в P1 первым делом; запись в `SECRETS_MAP.md` без значения |
| **R9** | Ключ OpenRouter не работает / нет баланса — выясняется только на P3 | средняя | 5 фаз работы впустую | **Оптимизация: 10-минутная проба ключа в P0**, до P1. Дешёвый способ снять гейт P3 заранее |

---

## 7. Порядок исполнения

1. **P0** — preflight + ранняя проба ключа OpenRouter (R9) + фиксация ограничений среды в `PRE-FLIGHT.md`.
2. **P1** — только после подтверждения B7. Ротация B6 первым действием.
3. **P2** — заморозить OpenAPI panel API в конце фазы → отдать офлайн-треку панель.
4. **P3** — гейт. FAIL → стоп, не идти в P4.
5. **P4 → P5 → приёмка A.** Первая точка реальной пользы: владелец пишет в Telegram и получает ответ модели.
6. **P6 → P6.5 → P7 → P7.5 → P8**, панель приезжает из офлайн-трека собранной.
7. **P9–P12**, затем пауза 7 дней.
8. **P13 → P14 → P15.**

Каждая фаза: inventory → checkpoint → изменение → тесты → evidence → PASS → следующая без ожидания «продолжай».
Checkpoints — `/opt/helm-state/implementation/checkpoints/Px-<ts>/`, **без plaintext-секретов** внутри tar.

## 8. Проверка

```bash
/opt/helm/tests/run-acceptance.sh --suite all    # §30.1–30.12 → docs/TEST_REPORT.md
```

READY объявляется только при: реальный `Hermes → LiteLLM → OpenRouter → model`; RED заблокирован без approval
и исполнен ровно один раз после него; restore test PASS; auth панели работает; Forgejo → GitHub CI по точному
SHA; независимое ревью пройдено; handoff готов. **Evidence before assertion.**
