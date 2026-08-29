#!/bin/bash
# Вторая волна разведки для плагина max-bridge (ADR-020). Read-only.
# Запуск: bash /tmp/hermes-recon-2.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. PlatformRegistry целиком ==='
sed -n '/class PlatformRegistry/,/^class /p' gateway/platform_registry.py | head -100

echo
echo '=== 2. PluginManager: доступные хуки и что передаётся в register(ctx) ==='
grep -rn 'class PluginManager\|class PluginContext\|def register_hook\|VALID_HOOKS\|KNOWN_HOOKS\|startup\|on_start\|gateway_ready' hermes_cli/plugins.py | head -30

echo
echo '=== 3. PluginContext — методы и поля ==='
sed -n '/class PluginContext/,/^class /p' hermes_cli/plugins.py | head -80

echo
echo '=== 4. invoke_hook в lifecycle.py ==='
grep -n 'def invoke_hook' -A 30 hermes_cli/lifecycle.py | head -40

echo
echo '=== 5. BasePlatformAdapter.send — полная сигнатура и докстрока ==='
sed -n '4100,4160p' gateway/platforms/base.py

echo
echo '=== 6. как self.adapters[platform] заполняется при старте ==='
sed -n '13330,13365p' gateway/run.py

echo
echo '=== 7. существующие plugins/platforms — пример, если есть ==='
ls plugins/platforms/ 2>&1
