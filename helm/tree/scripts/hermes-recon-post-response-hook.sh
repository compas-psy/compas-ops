#!/bin/bash
# Разведка для F-260829-25 (§14.14 paid-avoidance metric для Telegram).
# Read-only. Ищем: есть ли у PluginManager хук, который срабатывает
# ПОСЛЕ того, как LLM ответила (а не до, как pre_gateway_dispatch/
# pre_llm_call, которые уже используются в helm-control/__init__.py).
# Запуск: bash /tmp/hermes-recon-post-response-hook.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. все точки вызова _invoke_hook_callback (файл:строка) ==='
grep -rn '_invoke_hook_callback' --include=*.py .

echo
echo '=== 2. все имена хуков, переданные первым позиционным/именованным аргументом ==='
grep -rhoE "_invoke_hook_callback\([^,)]*[\"'][a-zA-Z_:]+[\"']" --include=*.py . | sort -u

echo
echo '=== 3. определение _invoke_hook_callback целиком ==='
grep -n 'def _invoke_hook_callback' -A 30 hermes_cli/plugins.py

echo
echo '=== 4. все PLUGIN_HOOK-подобные константы/списки допустимых хуков ==='
grep -rn 'pre_gateway_dispatch\|pre_llm_call\|post_gateway_dispatch\|post_llm_call\|on_response\|agent_response\|response_ready' --include=*.py hermes_cli/ gateway/ | grep -v '/tests/'

echo
echo '=== 5. где gateway/run.py вызывает pre_gateway_dispatch — контекст вокруг вызова LLM в той же функции ==='
grep -n 'pre_gateway_dispatch\|pre_llm_call\|def ' gateway/run.py | grep -B2 -A40 'pre_gateway_dispatch' | head -100
