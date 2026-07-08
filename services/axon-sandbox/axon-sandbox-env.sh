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
    local first_word
    first_word=$(echo "$cmd" | awk '{print $1}')
    
    # We only intercept executable files in user paths (Downloads, Documents, Desktop, local path)
    if [[ "$first_word" == ./* || "$first_word" == "$HOME/Downloads/"* || "$first_word" == "$HOME/Documents/"* || "$first_word" == "$HOME/Desktop/"* ]]; then
        if [[ -f "$first_word" && -x "$first_word" ]]; then
            local real_path
            real_path=$(realpath "$first_word" 2>/dev/null)
            
            # Do not intercept if not found or system path
            if [[ -z "$real_path" || "$real_path" == /usr/* || "$real_path" == /bin/* || "$real_path" == /sbin/* ]]; then
                return 0
            fi
            
            # Query decision via D-Bus org.axonos.Sandbox
            local decision
            # Bounded reply timeout: the default (25 s) freezes the shell when
            # the sandbox service is slow to activate or waits on a GUI prompt.
            decision=$(dbus-send --session --reply-timeout=5000 --dest=org.axonos.Sandbox --print-reply=literal /org/axonos/Sandbox org.axonos.Sandbox.AuditAndPrompt string:"$real_path" 2>/dev/null | xargs)
            
            if [[ "$decision" == "sandbox" ]]; then
                echo -e "\n\e[1;35m⬡ Rogue Software Shield: Running inside secure read-only sandbox...\e[0m"
                AXON_IN_SANDBOX=1 /usr/local/bin/axon-run "$cmd"
                return 1 # Skip original command execution
            elif [[ "$decision" == "block" ]]; then
                echo -e "\n\e[1;31m⬡ Rogue Software Shield: Execution blocked.\e[0m"
                return 1 # Skip original command execution
            fi
        fi
    fi
    return 0
}

# Enable extdebug to allow DEBUG trap to skip command execution
shopt -s extdebug
trap 'axon_sandbox_trap' DEBUG
