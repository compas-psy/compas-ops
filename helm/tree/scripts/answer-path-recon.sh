#!/bin/bash
# HELM · почему бот не ответил на живой вопрос.
#
# action=recon: в БД не пишет. probe() читает и может записать строку
# knowledge_answer_runs при LOCAL_ANSWER — поэтому здесь сессия
# закрывается rollback'ом, а не коммитом.
#
# Повод: владелец 06.09.2026 спросил бота «Какие виды врачей я посещал
# в 2025 году?» и через две минуты «Ты живой?» — ответа не было ни на
# один. Три места, где ответ мог не родиться, и все три проверяются:
#
#   1. шлюз. hermes-gateway — systemd-юнит, а не контейнер, в
#      `docker compose ps` его нет по построению, и «его нет в списке»
#      ничего не доказывает. Спрашиваем systemd напрямую;
#   2. знание. probe() на том же вопросе: что вернул бы локальный слой;
#   3. маршрут. какой веткой пошёл бы router и что он вообще умеет —
#      вопрос «в 2025 году» это врачи ПЛЮС фильтр по времени.
#
# Журнал печатается с маскировкой: длинные буквенно-цифровые строки и
# `bot<цифры>:` заменяются, чтобы токен не утёк в общий лог Actions
# (CLAUDE.md §5.4). Имена файлов и цитаты владельца печатаются — его
# решение от 05.09.2026.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. ШЛЮЗ ############"
sudo systemctl is-enabled hermes-gateway 2>&1 | sed 's/^/  is-enabled: /'
sudo systemctl is-active hermes-gateway 2>&1 | sed 's/^/  is-active:  /'
sudo systemctl show hermes-gateway \
  -p ActiveState -p SubState -p NRestarts -p ExecMainStartTimestamp \
  -p Result 2>&1 | sed 's/^/  /'

echo
echo "  --- ошибки в журнале за 12 часов (замаскировано) ---"
sudo journalctl -u hermes-gateway --since '12 hours ago' --no-pager 2>/dev/null \
  | grep -Ei 'traceback|error|critical|exception|refused|timeout' \
  | tail -25 \
  | sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot<ЗАМАСКИРОВАНО>/g; s/[A-Za-z0-9_-]{32,}/<ЗАМАСКИРОВАНО>/g' \
  | cut -c1-220 \
  | sed 's/^/  /' \
  || echo "  журнал недоступен"

echo
echo "  --- доступ к api.telegram.org с хоста ---"
# Telegram у нас за туннелем: httpx.ConnectError означает «туннеля нет»,
# а не «Telegram лежит». Проверяем сам туннель, а не гадаем.
for unit in sing-box tun2socks xray v2ray warp-svc; do
  state=$(sudo systemctl is-active "$unit" 2>/dev/null || true)
  [ -n "$state" ] && [ "$state" != "inactive" ] && echo "  $unit: $state"
done
echo -n "  прямой connect к api.telegram.org:443: "
timeout 8 bash -c 'cat < /dev/null > /dev/tcp/api.telegram.org/443' 2>/dev/null \
  && echo "открыт" || echo "НЕ открыт"
echo -n "  локальный прокси 127.0.0.1:18080: "
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/127.0.0.1/18080' 2>/dev/null \
  && echo "слушает" || echo "НЕ слушает"

echo
echo "############ 2. ЗНАНИЕ: probe() на вопросе владельца ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Что вернул бы локальный слой на тот самый вопрос."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe
from helm_core.knowledge.tenancy import bind_knowledge_user

QUESTIONS = [
    "Какие виды врачей я посещал в 2025 году?",
    "Каких врачей я посещал?",
]

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
with sessionmaker(engine, expire_on_commit=False)() as session:
    tenant = bind_knowledge_user(session, None)
    for question in QUESTIONS:
        print(f"  вопрос: «{question}»")
        try:
            result = probe(session, query=question, knowledge_user_id=tenant)
        except Exception as exc:  # noqa: BLE001 — печатаем и идём дальше
            print(f"    probe() упал: {type(exc).__name__}: {exc}")
            session.rollback()
            continue
        # Поле называется answer_text. В первой редакции скрипта я читал
        # `answer`, которого нет, и печатал «(пусто)» на любом ответе —
        # то есть измерял свой же getattr, а не продукт.
        print(f"    outcome:  {result.outcome}")
        print(f"    mode:     {result.mode}")
        answer = (result.answer_text or "").strip()
        print(f"    ответ:    {answer[:500] if answer else '(ПУСТО)'}")
        evidence = result.evidence or []
        print(f"    фрагментов: {len(evidence)}")
        print(f"    из памяти:  {len(result.memory or [])}")
        for item in evidence[:3]:
            text = " ".join((getattr(item, "chunk_text", "") or "").split())[:140]
            print(f"      — «{text}»")
        print()
    session.rollback()
PYEOF

echo "############ 3. МАРШРУТ ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Какой веткой пошёл бы router и умеет ли он фильтр по году."""
from helm_core.knowledge import query_router as R

QUESTIONS = [
    "Какие виды врачей я посещал в 2025 году?",
    "Каких врачей я посещал?",
]
for question in QUESTIONS:
    verdict = None
    for name in ("classify", "route", "detect_intent", "match_intent"):
        fn = getattr(R, name, None)
        if callable(fn):
            try:
                verdict = (name, fn(question))
                break
            except Exception as exc:  # noqa: BLE001
                verdict = (name, f"упало: {type(exc).__name__}: {exc}")
                break
    print(f"  «{question}» → {verdict}")
print()
print("  публичные имена router (для сверки, что вообще есть):")
print("   ", [n for n in dir(R) if not n.startswith("_")][:40])
PYEOF

echo "############ ГОТОВО ############"
