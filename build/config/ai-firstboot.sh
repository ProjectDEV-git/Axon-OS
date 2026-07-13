#!/usr/bin/env bash
# Axon OS — AI first-boot provisioner.
# Runs once as root on the first boot of an installed system (systemd oneshot
# axon-ai-firstboot.service) when the installer deferred AI setup, e.g. the
# install happened offline. Reads /etc/axon/ai-setup.json, installs Ollama
# and pulls the chosen default model, then removes the marker file.
set -euo pipefail

SETUP_JSON="/etc/axon/ai-setup.json"
LOG="/var/log/axon-ai-firstboot.log"

exec >>"${LOG}" 2>&1
echo "=== axon-ai-firstboot $(date -Iseconds) ==="

[[ -f "${SETUP_JSON}" ]] || { echo "no ${SETUP_JSON}; nothing to do"; exit 0; }

INSTALL_OLLAMA="$(python3 -c "import json;print(json.load(open('${SETUP_JSON}')).get('install_ollama', False))" 2>/dev/null || echo "False")"
MODEL="$(python3 -c "import json;print(json.load(open('${SETUP_JSON}')).get('ollama_model', 'llama3.2:3b'))" 2>/dev/null || echo "llama3.2:3b")"

if [[ "${INSTALL_OLLAMA}" != "True" ]]; then
    echo "install_ollama disabled; cleaning up"
    rm -f "${SETUP_JSON}"
    exit 0
fi

# Wait for the network (up to 5 minutes); leave the marker in place so a
# later boot retries if we never get online.
echo "waiting for network..."
for _ in $(seq 1 60); do
    if curl -sf --max-time 5 https://ollama.com >/dev/null 2>&1; then
        break
    fi
    sleep 5
done
if ! curl -sf --max-time 5 https://ollama.com >/dev/null 2>&1; then
    echo "still offline — will retry on next boot"
    exit 0
fi

echo "installing Ollama (idempotent — safe to re-run)..."
# SECURITY: curl | sh executes remote code without integrity verification.
# TODO: pin a SHA-256 hash of the installer and verify before execution.
# See: https://cheatsheetseries.owasp.org/cheatsheets/Secure\_Command\_Execution\_Cheat\_Sheet.html
if ! curl -fsSL https://ollama.com/install.sh | sh; then
        echo "Ollama install failed — will retry on next boot"
        exit 0
fi

systemctl enable --now ollama.service 2>/dev/null || true

echo "waiting for Ollama API..."
for _ in $(seq 1 30); do
    curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 2
done

echo "pulling default model: ${MODEL}"
for _ in 1 2 3; do
    if ollama pull "${MODEL}"; then
        rm -f "${SETUP_JSON}"
        echo "AI first-boot setup complete"
        exit 0
    fi
    sleep 10
done

echo "model pull failed — will retry on next boot"
exit 0
