#!/bin/bash
# Третья волна разведки для F-260829-25. Read-only.
# recon-2 подтвердил: post_llm_call реально вызывается (agent/
# turn_finalizer.py:624-643), payload несёт как минимум session_id,
# task_id, model, platform, assistant_response (по коду и по коду
# плагина langfuse). НЕ подтверждено: проходят ли Telegram-сообщения
# (gateway/run.py) через тот же turn_finalizer.py, или у gateway свой,
# отдельный путь вызова LLM, который turn_finalizer.py не использует —
# это решает, сработает ли post_llm_call для Telegram вообще.
# Запуск: bash /tmp/hermes-recon-post-response-hook-3.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. точный payload вызова post_llm_call (turn_finalizer.py:600-650) ==='
sed -n '590,650p' agent/turn_finalizer.py

echo
echo '=== 2. кто вызывает turn_finalizer (по всему дереву) — включая gateway? ==='
grep -rn 'turn_finalizer\|finalize_turn\|TurnFinalizer' --include=*.py . | grep -v '/tests/' | grep -v '^\./agent/turn_finalizer.py'

echo
echo '=== 3. _handle_message_with_agent (gateway/run.py) — вызывает ли что-то из agent/? ==='
sed -n '/async def _handle_message_with_agent/,/^    async def \|^    def /p' gateway/run.py | head -150

echo
echo '=== 4. session_id в post_llm_call — тот же смысл, что source.chat_id в pre_gateway_dispatch? ==='
grep -n 'session_id' gateway/run.py | grep -i 'chat_id\|source\.' | head -20
