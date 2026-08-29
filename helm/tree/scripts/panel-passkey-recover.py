#!/usr/bin/env python3
"""Восстановление доступа к панели при утере passkey (ТЗ §10.5.8.2).

Разворачивается на сервере как /opt/helm/scripts/panel-passkey-recover.
Требует root — это единственная защита: скрипт напрямую пишет в БД Control
Plane через `docker exec ... psql`, минуя API и HMAC-подпись сервисных
вызовов. Тот же способ обращения к БД, что и у остальных host-side
скриптов этого репозитория (check_spend.sh и т.п.) — не требует ставить
python-клиент Postgres на хост отдельно от контейнеров.

Делает:
  1. отзывает все активные panel_sessions владельца;
  2. по выбору — отзывает один или несколько WebauthnCredential (например,
     утерянное устройство, если рабочих осталось несколько);
  3. выпускает новый одноразовый enrollment-токен (TTL 30 минут, в БД
     хранится только его SHA256-хэш);
  4. печатает токен РОВНО ОДИН РАЗ в терминал — он никогда не пишется в лог,
     файл или Telegram/MAX (§10.5.8.2: "recovery token никогда не уходит
     автоматически");
  5. пишет аудиторскую запись (без самого токена) в /var/log/helm/panel-recovery.log.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SECRETS_DIR = Path("/etc/helm/secrets")
AUDIT_LOG = Path("/var/log/helm/panel-recovery.log")
ENROLLMENT_TTL_MINUTES = 30
POSTGRES_CONTAINER = "helm-postgres-1"


def _read_secret(name: str) -> str:
    path = SECRETS_DIR / name
    if not path.exists():
        sys.exit(f"секрет {path} не найден — скрипт запускается на самом сервере HELM")
    return path.read_text(encoding="utf-8").strip()


def _psql(sql: str, variables: dict[str, str]) -> str:
    """Выполнить SQL внутри контейнера Postgres.

    Значения подставляются только через `-v name=value` + `:'name'` в самом
    SQL — так psql сам безопасно экранирует строку как SQL-литерал, ручная
    склейка строк с интерполяцией сюда не допускается. SQL передаётся через
    stdin, не `-c`: `:'name'`-подстановка в `-c` psql не выполняет вовсе
    (проверено — там это просто синтаксическая ошибка), а через stdin
    работает штатно.
    """
    cmd = ["docker", "exec", "-i", POSTGRES_CONTAINER, "psql", "-U", "helm", "-d", "helm",
          "-v", "ON_ERROR_STOP=1", "-tAq"]
    for name, value in variables.items():
        cmd += ["-v", f"{name}={value}"]
    result = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"psql завершился с ошибкой:\n{result.stderr}")
    return result.stdout


def _audit(line: str) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
    AUDIT_LOG.chmod(0o600)


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("требуется root: sudo /opt/helm/scripts/panel-passkey-recover")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    parser.add_argument("--revoke-all-credentials", action="store_true",
                        help="отозвать ВСЕ passkey владельца, не только выбранные интерактивно")
    args = parser.parse_args()

    owner_id = _read_secret("telegram_owner_id")

    rows = _psql(
        "SELECT id, coalesce(label, ''), created_at FROM webauthn_credentials "
        "WHERE owner_id = :'owner_id' AND revoked_at IS NULL ORDER BY created_at;",
        {"owner_id": owner_id},
    )
    credentials = [line.split("|", 2) for line in rows.splitlines() if line.strip()]

    print(f"Владелец: {owner_id}")
    print(f"Активных passkey: {len(credentials)}")
    for i, (cred_id, label, created_at) in enumerate(credentials):
        print(f"  [{i}] {label or '(без метки)'} — создан {created_at}")

    to_revoke: list[str] = []
    if args.revoke_all_credentials:
        to_revoke = [row[0] for row in credentials]
    elif credentials:
        raw = input(
            "Номера passkey для отзыва через запятую (Enter — не отзывать ни одного): "
        ).strip()
        if raw:
            indices = {int(part) for part in raw.split(",") if part.strip()}
            to_revoke = [credentials[i][0] for i in indices if 0 <= i < len(credentials)]

    print()
    print("Будет сделано:")
    print(f"  - отозваны ВСЕ активные panel_sessions владельца {owner_id}")
    print(f"  - отозвано passkey: {len(to_revoke)} из {len(credentials)}")
    print(f"  - выпущен новый enrollment-токен (TTL {ENROLLMENT_TTL_MINUTES} минут)")
    if not args.yes and input("Продолжить? [yes/N] ").strip().lower() != "yes":
        sys.exit("отменено")

    now = datetime.now(timezone.utc)
    operator = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"

    revoked_rows = _psql(
        "UPDATE panel_sessions SET revoked_at = :'now' "
        "WHERE owner_id = :'owner_id' AND revoked_at IS NULL RETURNING id;",
        {"now": now.isoformat(), "owner_id": owner_id},
    )
    sessions_revoked = len([line for line in revoked_rows.splitlines() if line.strip()])

    if to_revoke:
        _psql(
            "UPDATE webauthn_credentials SET revoked_at = :'now' "
            "WHERE id = ANY(string_to_array(:'ids', ',')::uuid[]);",
            {"now": now.isoformat(), "ids": ",".join(to_revoke)},
        )

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    _psql(
        "INSERT INTO panel_enrollment_tokens (id, token_hash, owner_id, expires_at) "
        "VALUES (:'id', :'token_hash', :'owner_id', :'expires_at');",
        {
            "id": str(uuid.uuid4()), "token_hash": token_hash, "owner_id": owner_id,
            "expires_at": (now + timedelta(minutes=ENROLLMENT_TTL_MINUTES)).isoformat(),
        },
    )

    _audit(
        f"operator={operator} owner={owner_id} sessions_revoked={sessions_revoked} "
        f"credentials_revoked={len(to_revoke)} enrollment_token_issued=true"
    )

    print()
    print("Готово. Новый enrollment-токен (показывается один раз, никуда не логируется):")
    print()
    print(f"    {raw_token}")
    print()
    print(f"Действителен {ENROLLMENT_TTL_MINUTES} минут. Передайте его владельцу лично —")
    print("не через Telegram/MAX (§10.5.8.2). Он откроет /login?step=enroll после")
    print("повторного входа через Telegram OIDC.")


if __name__ == "__main__":
    main()
