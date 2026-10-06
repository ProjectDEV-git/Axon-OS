# Axon OS - Rogue Software Shield Integration
# Sourced by interactive bash shells to intercept untrusted scripts.

# Only hook interactive bash shells. Without this guard the DEBUG trap and
# extdebug get installed into every login shell — including GDM/session
# startup scripts — where the per-command trap overhead and blocking D-Bus
# calls stall the whole login. POSIX syntax: dash also sources profile.d.
[ -n "${BASH_VERSION:-}" ] || return 0
case $- in
    *i*) ;;
    *) return 0 ;;
esac

axon_sandbox_trap() {
    # Guard against nested calls or empty commands
    if [[ "${AXON_IN_SANDBOX:-0}" -eq 1 || -z "${BASH_COMMAND:-}" ]]; then
        return 0
    fi

    local cmd="$BASH_COMMAND"
    local -a words
    read -r -a words <<< "$cmd" || true
    local target="${words[0]:-}"
    local via_interpreter=false

    # `bash evil.sh`, `python3 evil.py` ...: audit the script argument
    case "${target##*/}" in
        bash|sh|dash|zsh|ksh|python|python3|perl|ruby|node)
            local w
            for w in "${words[@]:1}"; do
                [[ "$w" == -* ]] && continue
                target="$w"
                via_interpreter=true
                break
            done
            [[ "$via_interpreter" == true ]] || return 0
            ;;
    esac

    # BASH_COMMAND is not tilde-expanded yet
    target="${target/#\~/$HOME}"
    [[ -f "$target" ]] || return 0
    if [[ "$via_interpreter" == false && ! -x "$target" ]]; then
        return 0
    fi

    local real_path
    real_path=$(realpath "$target" 2>/dev/null) || return 0

    # Intercept user-writable locations where downloaded or dropped files land
    case "$real_path" in
        "$HOME"/*|/tmp/*|/var/tmp/*|/dev/shm/*) ;;
        *) return 0 ;;
    esac

    # Query decision via D-Bus org.axonos.Sandbox. The service may wait for the
    # user to answer a dialog, so allow time for that.
    local decision
    decision=$(dbus-send --session --reply-timeout=120000 --dest=org.axonos.Sandbox --print-reply=literal /org/axonos/Sandbox org.axonos.Sandbox.AuditAndPrompt string:"$real_path" 2>/dev/null | xargs)

    case "$decision" in
        allow)
            return 0
            ;;
        sandbox)
            echo -e "\n\e[1;35m⬡ Rogue Software Shield: Running inside secure sandbox (no network, empty home)...\e[0m"
            AXON_IN_SANDBOX=1 /usr/local/bin/axon-run --file "$real_path" "$cmd"
            return 1 # Skip original command execution
            ;;
        block)
            echo -e "\n\e[1;31m⬡ Rogue Software Shield: Execution blocked.\e[0m"
            return 1
            ;;
        *)
            # Fail closed: no answer, a timeout, "deny" or anything unexpected
            echo -e "\n\e[1;31m⬡ Rogue Software Shield: could not verify ${real_path}; not running it.\e[0m" >&2
            echo "  To run it sandboxed anyway: axon-run --file '${real_path}' '${cmd//\'/}'" >&2
            return 1
            ;;
    esac
}

# Enable extdebug to allow DEBUG trap to skip command execution
shopt -s extdebug
trap 'axon_sandbox_trap' DEBUG
