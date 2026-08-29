# Фикс: helm-core не достаёт до Hermes API через 127.0.0.1

Живой тест 29.08.2026 показал: даже с секретом на месте и правильным
кодом, вызов `http://127.0.0.1:8642/v1/responses` из контейнера
`helm-core` падал с `URLError`. Причина архитектурная, не опечатка:
Hermes работает на хосте, Control Plane — в Docker-контейнере со своим
network namespace; `127.0.0.1` внутри контейнера — это сам контейнер.

Проверен файрвол сервера ПЕРЕД этим фиксом (`network-recon.sh`): `ufw`
активен, `default deny incoming`, разрешены только `22/80/443`. Фикс
ниже не меняет это — публичный интернет как был закрыт, так и остаётся;
новое правило открывает 8642 **только** для подсети докер-моста.

## 1. Переключить Hermes на 0.0.0.0

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo API_SERVER_HOST=0.0.0.0 | sudo tee -a /home/helm/.hermes/.env > /dev/null && sudo chown helm:helm /home/helm/.hermes/.env && sudo systemctl restart hermes-gateway && sleep 5 && sudo ss -tlnp | grep 8642"
```

Ожидается `0.0.0.0:8642` вместо `127.0.0.1:8642`.

## 2. Точечное правило ufw — только для докер-моста

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo ufw allow from 172.18.0.0/16 to any port 8642 proto tcp comment 'hermes API — только докер-мост helm' && sudo ufw status verbose"
```

В выводе должна появиться строка вида `8642/tcp ALLOW IN 172.18.0.0/16`
— без неё, без явного `ALLOW`, только смена bind-адреса ничего не даст:
`ufw`/`iptables` по-прежнему блокирует INPUT для всего, что не в списке.

**Если подсеть отличается от `172.18.0.0/16`** (сеть Docker могла
пересоздаться) — сначала уточнить актуальную:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "docker network inspect helm_default --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'"
```

и подставить её в команду выше вместо `172.18.0.0/16`.

## 3. Доставить и задеплоить обновлённый код

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\compose\docker-compose.yml helm@185.250.44.137:/tmp/
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/control-plane/helm_core && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo mv /tmp/docker-compose.yml /opt/helm/compose/docker-compose.yml && sudo chown -R root:root /opt/helm/control-plane/helm_core /opt/helm/compose/docker-compose.yml && sudo chmod -R go-w /opt/helm/control-plane/helm_core && cd /opt/helm/compose && sudo docker compose build helm-core && sudo docker compose up -d --force-recreate helm-core && sleep 15 && sudo docker compose ps helm-core"
```

## 4. Проверка изнутри контейнера — до живого MAX

Скриптом, не строкой: python-однострочник с вложенными кавычками через
`ssh` в PowerShell разваливается на экранировании — та же ловушка, что
уже несколько раз ловилась на этом сервере.

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\check-hermes-reachable.sh helm@185.250.44.137:/tmp/
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo bash /tmp/check-hermes-reachable.sh"
```

Ожидается `200`. Это proof того, что контейнер реально достаёт до
хоста — до того, как тратить попытку на живое сообщение в MAX.

## 5. Проверка снаружи — что публичный доступ НЕ появился

Обязательный шаг, не пропускать:

```powershell
curl -m 5 http://185.250.44.137:8642/health
```

Ожидается **таймаут или отказ соединения** (не ответ). Если что-то
пришло — сразу откатить правило ufw (`sudo ufw delete allow from
172.18.0.0/16 to any port 8642 proto tcp`) и разбираться, что не так,
прежде чем продолжать.

## 6. Живой тест через MAX

Написать боту любое сообщение. Теперь должен прийти настоящий ответ
chief-агента.
