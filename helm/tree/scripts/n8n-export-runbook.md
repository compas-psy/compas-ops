# Ранбук: выгрузка workflow n8n и бэкап Forgejo (§17.5, §18.7)

Подготовлено 29.08.2026. Закрывает два пункта, которые до сих пор
оставались открытыми: `export/restore` из чек-листа P6 и включение
Forgejo в тот же restic-бэкап (распоряжение владельца от 29.08.2026).

Ключ подключения везде указывается явно:
`-i "C:\Users\eliah\.ssh\helm_deploy_key"`.

## 1. Ключ API n8n

Нужен, чтобы выгрузка вообще могла обратиться к n8n. Создаётся в самом
n8n через SSH-туннель:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" -L 5678:127.0.0.1:5678 helm@185.250.44.137
```

Дальше в браузере `http://127.0.0.1:5678` → Settings → n8n API →
Create an API key (срок — максимальный из предложенных; при истечении
ночная выгрузка начнёт писать в лог ошибку, но бэкап не сломается).

Ключ кладётся на сервер, в переписку не попадает:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'КЛЮЧ_API' | sudo tee /etc/helm/secrets/n8n_api_key > /dev/null && sudo chown root:root /etc/helm/secrets/n8n_api_key && sudo chmod 600 /etc/helm/secrets/n8n_api_key && ls -la /etc/helm/secrets/n8n_api_key"
```

Права здесь `600 root:root`, а не `640 root:helm-secrets`: этот секрет
читает host-скрипт от root (как `restic_password`), а не контейнер.
Перепутать категории — значит либо сломать доступ, либо ослабить права
без нужды (F-260829-09).

## 2. Доставка скриптов на сервер

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\n8n-workflows.py helm\tree\scripts\backup.sh helm\tree\scripts\restore_test.sh helm@185.250.44.137:/tmp/
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo mv /tmp/n8n-workflows.py /tmp/backup.sh /tmp/restore_test.sh /opt/helm/scripts/ && sudo chown root:root /opt/helm/scripts/n8n-workflows.py /opt/helm/scripts/backup.sh /opt/helm/scripts/restore_test.sh && sudo chmod 755 /opt/helm/scripts/n8n-workflows.py /opt/helm/scripts/backup.sh /opt/helm/scripts/restore_test.sh && sudo mkdir -p /opt/helm/n8n/exports"
```

## 3. Проверка выгрузки

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo python3 /opt/helm/scripts/n8n-workflows.py export && ls -la /opt/helm/n8n/exports/"
```

Пока в n8n нет ни одного workflow, ожидаемый вывод — «всего workflow: 0»
и пустой каталог. Это не ошибка: скрипт проверяется на том, что он
доходит до API и получает ответ, а не на количестве файлов.

## 4. Проверка бэкапа целиком (после того, как Forgejo уже поднят)

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo /opt/helm/scripts/backup.sh"
```

Ожидается `BACKUP DONE` и отсутствие строки про ненайденный каталог
Forgejo. Затем restore-test — он теперь проверяет и репозитории Forgejo
(refs, tags, HEAD, `git fsck`):

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo /opt/helm/scripts/restore_test.sh"
```

До миграции репозиториев тест напечатает «репозиториев Forgejo в
снапшоте нет — миграция ещё не выполнена» и пройдёт: проверять нечего.
После миграции (`forgejo-migrate-runbook.md`) этот же тест начнёт
проверять реальный репозиторий сам, без правок.

## 5. Выгрузка в git (§17.5: «→ Git»)

`/opt/helm` — не git-репозиторий (F-260829-01), поэтому выгрузка
попадает в историю не сама. Когда в n8n появятся первые workflow:

```powershell
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm@185.250.44.137:/opt/helm/n8n/exports/* D:\ПРОЕКТЫ\simpas\helm\compas-ops\helm\tree\n8n\exports\
```

и обычный коммит. До тех пор в бэкапе выгрузка уже есть — restic
забирает `/opt/helm/n8n/exports` каждую ночь.
