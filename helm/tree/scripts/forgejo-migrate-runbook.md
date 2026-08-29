# Ранбук: миграция репозиториев GitHub → Forgejo (ТЗ §18.3)

Подготовлено 29.08.2026. Скрипт: `scripts/forgejo-migrate.py` (шаги 1–9
чек-листа §18.3 для каждого репозитория; подробности — в его docstring).
Выполняется одним батчем с машины владельца (Windows PowerShell).

## Что уже проверено при подготовке

- Все 5 репозиториев §18.2 существуют на GitHub и **публичные** —
  клонирование не требует токена; PAT нужен только для push mirror
  (запись в GitHub со стороны Forgejo).
- `helm-infra` на GitHub не существует — по P6.5 это опциональный импорт
  «for future history», в этот батч не входит.
- `compas-ops` мигрируется **отдельным последним запуском** после merge
  текущей рабочей ветки: push mirror перезаписывает refs на GitHub
  состоянием Forgejo, а на compas-ops прямо сейчас идёт разработка —
  включённый mirror затёр бы свежие GitHub-коммиты.

## Правило заморозки

С момента запуска скрипта GitHub-копии мигрированных репозиториев —
только зеркало: любой push в GitHub там будет перезаписан следующим
sync. Пушить — только в Forgejo (после переключения remotes, шаг 11).

## Батч (по порядку)

### 1. Создать GitHub PAT (в браузере, один раз)

github.com → Settings → Developer settings → Fine-grained tokens →
Generate new token:

- Resource owner: `compas-psy`
- Repository access: Only select repositories → `compas-voice`,
  `cmpas.ru`, `zapiski`, `signalAI-mobileApp` (+ `compas-ops` — сразу,
  чтобы не выпускать второй токен для последнего батча)
- Permissions → Repository permissions → **Contents: Read and write**.
  Больше ничего (§18.4: fine-grained, selected repos only, Contents write).
- Срок — минимальный разумный (90 дней; mirror перестанет пушить после
  истечения — завести напоминание о ротации).

Если организация не разрешает fine-grained PAT — classic token со scope
`repo` (шире, чем хочется; отозвать после стабилизации mirror и заменить
fine-grained, когда включат).

### 2. Положить PAT на сервер (токен не покидает твой терминал)

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'ВСТАВЬ_PAT_СЮДА' | sudo tee /etc/helm/secrets/github_mirror_pat > /dev/null && sudo chmod 600 /etc/helm/secrets/github_mirror_pat && sudo chown root:root /etc/helm/secrets/github_mirror_pat"
```

### 3. Доставить скрипт на сервер

Сначала обновить локальную копию репозитория (из
`D:\ПРОЕКТЫ\simpas\helm\compas-ops`):

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" D:\ПРОЕКТЫ\simpas\helm\compas-ops\helm\tree\scripts\forgejo-migrate.py helm@185.250.44.137:/tmp/forgejo-migrate.py
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo mv /tmp/forgejo-migrate.py /opt/helm/scripts/forgejo-migrate.py && sudo chown root:root /opt/helm/scripts/forgejo-migrate.py && sudo chmod 755 /opt/helm/scripts/forgejo-migrate.py"
```

### 4. Запустить миграцию (4 репозитория, без compas-ops)

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo python3 /opt/helm/scripts/forgejo-migrate.py"
```

Скрипт сам: выпустит Forgejo-токен → создаст приватную организацию
`compas-psy` → для каждого репо: инвентаризация (refs, открытые PR,
workflow-триггеры, LFS) → миграция → сверка всех SHA → push mirror с
sync_on_commit → немедленный sync → повторная сверка SHA. Итог `PASS`
на каждом репо = шаги 1–9 закрыты. Полный вывод **вставить в чат** —
он уйдёт в migration log (§18.3: URL/history сохранить).

Повторный запуск безопасен: существующий репозиторий и настроенный
mirror распознаются и не дублируются.

### 5. compas-ops — отдельно, после merge рабочей ветки

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo python3 /opt/helm/scripts/forgejo-migrate.py compas-ops"
```

Не запускать, пока ветка `claude/ai-agents-server-deployment-xdp77a`
не влита: у compas-ops есть незавершённая работа, mirror её затрёт.

## Что остаётся после батча (не этот скрипт)

- **Шаг 10, prove CI**: для репо с workflow — тестовый push ветки в
  Forgejo → mirror доносит exact SHA в GitHub → Actions `push`-триггер
  отрабатывает → сверить `run.head_sha` с Forgejo SHA (§18.5). Скрипт
  печатает по каждому репо, какие workflow есть и какие триггеры
  упоминают — где нет `push`-триггера, добавить безопасный `push`/
  `workflow_dispatch` без изменения продуктового поведения.
- **Шаг 11, переключение primary remote** — только после PASS CI-пробы,
  отдельным решением владельца, по одному репозиторию.
- **Backup**: после миграции включить Forgejo (repos/DB/config/
  attachments) в restic (§18.7) и прогнать restore-test одного
  репозитория с проверкой refs/tags/HEAD (распоряжение владельца от
  29.08.2026). БД `forgejo` уже попадает в `pg_dumpall` существующего
  backup.sh; каталог репозиториев (volume `helm_forgejo_data`) — ещё нет.
- **helm-infra**: опциональный импорт истории /opt/helm — отдельным
  решением (P6.5: «optionally»).
