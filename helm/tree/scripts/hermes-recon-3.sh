#!/bin/bash
# Третья волна разведки для max-bridge (ADR-020). Read-only.
# Гипотеза: _dispatch_plugin_message_injection — уже готовый, штатный
# вход для плагина, внедряющего синтетическое сообщение, и он может
# снять весь вопрос о регистрации Platform/адаптера разом.
# Запуск: bash /tmp/hermes-recon-3.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. _dispatch_plugin_message_injection целиком ==='
awk '/async def _dispatch_plugin_message_injection/{p=1} p{print; if(/^    async def [a-zA-Z_]/ && !/_dispatch_plugin_message_injection/) exit}' gateway/run.py | head -150

echo
echo '=== 2. кто её вызывает ==='
grep -rn '_dispatch_plugin_message_injection' --include=*.py . | grep -v 'async def _dispatch_plugin_message_injection'

echo
echo '=== 3. PlatformRegistry.register (не register_deferred) ==='
grep -n '    def register(' -A 40 gateway/platform_registry.py | head -60

echo
echo '=== 4. где ещё используются Platform.WEBHOOK / Platform.API_SERVER ==='
grep -rn 'Platform\.WEBHOOK\|Platform\.API_SERVER' --include=*.py . | head -20
