#!/bin/bash
# Вторая волна разведки для F-260829-25. Read-only.
# recon-1 нашёл: "post_llm_call" — объявленное имя хука (hermes_cli/
# plugins.py, hermes_cli/hooks.py), в одном списке с уже используемым
# pre_llm_call. Не подтверждено: реально ли gateway где-то ВЫЗЫВАЕТ его
# (invoke_hook("post_llm_call", ...)), а не просто числит как допустимое
# имя — и какие поля несёт payload (нужен ли ответ модели, успех/провал,
# session_id для сопоставления с pre_llm_call).
# Запуск: bash /tmp/hermes-recon-post-response-hook-2.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. описание post_llm_call в реестре хуков (схема payload) ==='
sed -n '130,200p' hermes_cli/hooks.py

echo
echo '=== 2. все вызовы invoke_hook(...) во всём дереве — какие хуки реально дёргаются ==='
grep -rn 'invoke_hook(' --include=*.py . | grep -v '/tests/'

echo
echo '=== 3. конкретно post_llm_call — вызывается ли где-нибудь ==='
grep -rn 'post_llm_call' --include=*.py . | grep -v '/tests/'

echo
echo '=== 4. что вызывает invoke_hook("pre_llm_call", ...) — образец payload рядом по коду ==='
grep -rn 'invoke_hook("pre_llm_call"\|invoke_hook(.pre_llm_call.' --include=*.py . -A 15 | grep -v '/tests/'
