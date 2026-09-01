# MODEL_ROUTING — какая модель что делает и почему

Два независимых мира: платные модели через LiteLLM (только Hermes,
только по алиасу) и локальные модели без единого платного вызова
(Knowledge: embeddings/GigaAM/Ollama). Ни один код-путь не пересекает
эти два мира иначе как через явную эскалацию `probe()`→Hermes на
исчерпании бесплатного пути (см. `docs/KNOWLEDGE_RETRIEVAL.md`).

## Платные модели — LiteLLM, только по алиасу (ADR-003)

`config/models/litellm.yaml` — единственный источник правды. Hermes
никогда не видит `OPENROUTER_API_KEY`, только фиксированный алиас;
`scripts/provision_hermes_profiles.sh` выдаёт каждому профилю Hermes
ОДИН scoped virtual key на ОДИН алиас — профиль физически не видит
остальные.

| Алиас | Модель (провайдер) | Fallback | Статус |
|---|---|---|---|
| `helm-router` | `deepseek/deepseek-v4-flash-0731` | `helm-router-fallback` → `openai/gpt-5.6-luna` | provisional primary+fallback, §15.3 п.9 |
| `helm-cheap` | `deepseek/deepseek-v4-flash-0731` | `helm-cheap-fallback` → `openai/gpt-5.6-luna` | provisional |
| `helm-standard` | `z-ai/glm-5` | `helm-standard-fallback` → `deepseek/deepseek-v4-pro-0813` | provisional, реально используется chief-чатом |
| `helm-code` | `z-ai/glm-5` | нет | зарегистрирован для будущей фазы, primary/fallback не выбран |
| `helm-review` | `z-ai/glm-5.3` | нет | зарегистрирован для будущей фазы |
| `helm-board` | `openai/gpt-5.6-sol` | нет | зарегистрирован для будущей фазы (борд C-уровня) |
| `helm-longhorizon` | `z-ai/glm-5.3` | нет | зарегистрирован для будущей фазы |

Четыре нижних алиаса намеренно НЕ имеют второй записи под тем же
именем: LiteLLM балансирует между одноимёнными записями случайно, а
primary/fallback решение спека (§15.3 п.9) требует только для верхних
трёх на Milestone A — выбор для остальных откладывается до момента,
когда соответствующий профиль реально начнёт ими пользоваться.

Версии моделей — пины, не floating tags (§33 "pin versions") — даже
там, где провайдер предлагает плавающий алиас вроде `kimi-latest`.

**Сетевая особенность**: OpenRouter блокирует этот VPS по IP/датацентру
(Cloudflare 403 до авторизации) — обход только для этого трафика через
`sing-box+mieru` в Финляндию (`HTTPS_PROXY`/`HTTP_PROXY`, заданы ТОЛЬКО
для сервиса `litellm` в `docker-compose.yml`, не глобально — тот же
класс настройки, что уже дважды ловил живые баги, когда её забывали
распространить на новый сервис, см. `TROUBLESHOOTING.md`).

**`drop_params: true`** — найдено на первом реальном сообщении: Hermes
(provider=custom) безусловно шлёт `reasoning_effort` на каждый
completion-запрос; закреплённая версия `litellm:main-v1.55.8` не
принимает этот kwarg в пути OpenRouter вообще. `drop_params` чинит не
только эту модель, но и любую будущую под custom-провайдером Hermes —
выбрано вместо апгрейда закреплённой версии LiteLLM (§33).

## Локальные модели — Knowledge, ноль платного AI

Все выбраны живым замером на реальном 12GB VPS, не по репутации —
методология и цифры замера в `docs/KNOWLEDGE_MODELS.md`, здесь только
итог:

| Роль | Модель | Сервис/режим | Где решение |
|---|---|---|---|
| Dense retrieval embeddings | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim) | `helm-embed`, постоянный сервис, healthcheck, лимит 1.2ГБ | ADR-025 |
| Audio ASR | GigaAM `e2e_rnnt` | on-demand, `concurrency=1`, не резидентна | ADR-021 |
| VAD (голос) | Silero VAD | вместе с GigaAM-путём | ADR-021 (замена gated `pyannote/segmentation-3.0`) |
| Z2-рефраз (тон ответа) | `gemma2:2b` (Ollama) | `ollama`, one-shot runtime, `OLLAMA_KEEP_ALIVE=0` | `docs/KNOWLEDGE_MODELS.md`, живой замер 31.08.2026 |

Все четыре — `paid_ai_used=False` (§14.14): рефраз локальной моделью не
платный вызов, метрика не меняется.

## Как решается, эскалировать ли к платной модели

Единственный переход из бесплатного мира в платный — `probe()`
возвращает `NEEDS_REASONING`, дальше вопрос идёт по обычному
Telegram/MAX-пути к chief-агенту (`helm-standard`). Для KNOWLEDGE_USER
этот переход структурно невозможен (ADR-020) — не флаг, который можно
забыть проверить, а отсутствие импорта модельного клиента в модулях,
которые собирают ответ.

## Не в объёме этого документа

- Динамическая маршрутизация по типу вопроса (классификация сообщения →
  выбор алиаса) — отложена, см. ADR-003.
- `helm-code`/`helm-review`/`helm-board`/`helm-longhorizon` — primary/
  fallback выбор для них не сделан, эти алиасы ждут своей фазы.
