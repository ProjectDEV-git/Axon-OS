# This file is meant to be SOURCED by an interactive zsh or bash session,
# never executed directly. It uses bash array syntax (apex_win_argv=(),
# +=()) and zsh's ${=1} split operator, neither of which a plain POSIX
# `sh`/dash can even parse (confirmed: `sh -n` on this file fails with a
# syntax error on the bash array literal, well before execution). There is
# no `#!/bin/sh` shebang for that reason -- one here would be actively
# misleading about how this file can be invoked.
#
# apex-win-shell-hook.sh — opt-in terminal launch for `.exe` invocations on
# macOS, including `./game.exe` and bare `game.exe`.
#
# Linux intercepts this at the kernel level via binfmt_misc: no user
# opt-in needed, execve() on an MZ-header file transparently routes through
# win-sandbox-runner regardless of how it was invoked. macOS's execve()
# only understands Mach-O and #! scripts, and there is no user-registerable
# table the way binfmt_misc provides -- kernel-level interception of
# arbitrary executable formats is not possible without a kext, and kexts
# are deprecated and blocked by default on Apple Silicon. This is the
# closest userspace substitute.
#
# Why this is NOT just `command_not_found_handler`/`command_not_found_handle`:
# those hooks only fire when a shell's $PATH search for a *bare* command
# name fails (POSIX "command not found", exit 127). A command containing a
# slash -- `./game.exe`, which is how people actually type these -- is
# never PATH-searched at all: the shell attempts execve() on that exact
# path directly, and a non-executable Windows binary fails with EACCES
# ("permission denied", exit 126), a completely different code path that
# never reaches command-not-found handling. Verified directly (both zsh and
# bash, real MZ-header test files, both with and without the executable
# bit): the not-found hooks never fire for a slash-containing path,
# permission-denied or otherwise.
#
# What this actually uses instead:
#   zsh:  overrides the `accept-line` ZLE widget, which runs when Enter is
#         pressed on the command line, before the shell attempts to
#         execute anything. It can freely rewrite $BUFFER.
#   bash: installs a `DEBUG` trap (with `shopt -s extdebug`, which makes a
#         DEBUG trap's non-zero return actually skip the command instead
#         of merely being observed). $BASH_COMMAND is the command about to
#         run; returning 1 cancels it.
# Both were verified end-to-end against real invocations, including the
# `./game.exe` case command_not_found could not reach, using a real pty
# (`script(1)`) so this is proven interactive-shell behavior, not just a
# plausible-looking snippet.
#
# This is deliberately opt-in (source it yourself, no installer silently
# edits ~/.zshrc/~/.bashrc) and only affects *this* interactive shell: it
# does not catch exec() calls from other programs, scripts, GUI launchers,
# or non-interactive shells, and it is not equivalent to binfmt_misc.
#
# Usage: add one line to ~/.zshrc (or ~/.bashrc):
#   source /path/to/apex-win-shell-hook.sh
#
# Then:
#   $ ./game.exe --windowed
#   -> APEX-WIN: routing ./game.exe through win-sandbox-runner
#   [wine runs]

_apex_win_is_exe_command() {
    # $1 is the full command line (bash: $BASH_COMMAND; zsh: $BUFFER).
    # Matched against the first whitespace-separated word only, so this
    # does not misfire on something like `echo "run setup.exe later"`.
    first_word="${1%%[[:space:]]*}"
    case "$first_word" in
        *.exe|*.exe.|*.EXE|*.Exe) return 0 ;;
        *) return 1 ;;
    esac
}

# Rewrite a raw command line like "./game.exe --windowed --fullscreen" into
# the argv win-sandbox-runner expects: --exe <path> -- <passthrough args>.
# The `--` separator is required -- win-sandbox-runner's arg parser
# otherwise treats a leading `-`/`--` in a trailing arg as its own flag and
# refuses it (verified: `--exe x --dry-run --some-flag` errors with
# "unexpected argument", while inserting `--` first works).
#
# This is intentionally simple whitespace splitting, not a full shell
# tokenizer: quoted arguments containing spaces will not round-trip
# correctly. Documented as a known limitation rather than silently
# mishandled -- see the module doc comment's scope statement.
_apex_win_build_argv() {
    # zsh does NOT word-split an unquoted parameter expansion by default
    # (unlike bash/POSIX sh) -- `set -- $1` in zsh puts the entire string
    # into $1 as one word, silently. Verified directly: `x="a b c"; set --
    # $x; echo $#` prints 1 in zsh, 3 in bash. `${=1}` is zsh's
    # SH_WORD_SPLIT-equivalent one-off syntax for exactly this; bash/sh
    # already split correctly by default and don't understand `${=...}` at
    # all, so it must be behind a shell check, not used unconditionally.
    if [ -n "${ZSH_VERSION:-}" ]; then
        set -- ${=1}
    else
        set -- $1
    fi
    exe_path="$1"
    shift
    if [ "$#" -gt 0 ]; then
        printf '%s\n' "--exe" "$exe_path" "--" "$@"
    else
        printf '%s\n' "--exe" "$exe_path"
    fi
}

_apex_win_dispatch_line() {
    line="$1"
    first_word="${line%%[[:space:]]*}"

    if ! command -v win-sandbox-runner >/dev/null 2>&1; then
        echo "APEX-WIN: win-sandbox-runner not found on PATH, cannot run $first_word" >&2
        return 127
    fi

    echo "-> APEX-WIN: routing $first_word through win-sandbox-runner" >&2

    # This function is only ever called from the zsh accept-line widget or
    # the bash DEBUG trap below, both of which are themselves registered
    # only under `[ -n "$ZSH_VERSION" ]`/`[ -n "$BASH_VERSION" ]` -- so
    # exactly one of those two is guaranteed true here, and both shells
    # support the same array append syntax used below. No third branch is
    # reachable; an earlier version had one and it was genuinely dead code.
    #
    # Read the rewritten argv back as separate lines rather than expanding
    # _apex_win_build_argv's output inline: that avoids a second round of
    # (shell-dependent) word-splitting on any path or arg that itself
    # contains whitespace.
    argv_file=$(mktemp 2>/dev/null) || argv_file="/tmp/apex-win-argv-$$"
    _apex_win_build_argv "$line" > "$argv_file"

    # shellcheck disable=SC2034  # apex_win_argv is used via "${apex_win_argv[@]}"
    apex_win_argv=()
    while IFS= read -r arg; do
        apex_win_argv+=("$arg")
    done < "$argv_file"
    rm -f "$argv_file"
    win-sandbox-runner "${apex_win_argv[@]}"
}

# --- zsh: rewrite the command line itself before the shell ever tries to
# execute it, via the accept-line ZLE widget. ---
if [ -n "${ZSH_VERSION:-}" ]; then
    _apex_win_zsh_accept_line() {
        if _apex_win_is_exe_command "$BUFFER"; then
            _apex_win_dispatch_line "$BUFFER"
            # Consume the typed line without re-executing it: push an
            # empty, already-"run" no-op instead of the original .exe
            # invocation, then let zsh continue normally.
            BUFFER=""
            zle .accept-line
            return
        fi
        zle .accept-line
    }
    zle -N accept-line _apex_win_zsh_accept_line
fi

# --- bash: a DEBUG trap that can veto the pending command, gated by
# extdebug (without it, a DEBUG trap can only observe, not cancel). ---
if [ -n "${BASH_VERSION:-}" ]; then
    shopt -s extdebug

    _apex_win_bash_debug_trap() {
        # Guard against re-entrancy: the trap fires for every command,
        # including the ones this hook itself runs (mktemp, win-sandbox-
        # runner, etc.) via BASH_COMMAND -- only act on top-level, not
        # already-handled invocations.
        if [ -n "${_apex_win_in_dispatch:-}" ]; then
            return 0
        fi
        # No slash requirement here, unlike command_not_found_handle: the
        # DEBUG trap fires before bash has attempted to resolve
        # $BASH_COMMAND at all (function/builtin/$PATH search or direct
        # exec of a slash-containing path), so it sees both `game.exe` and
        # `./game.exe` equally, before either would fail differently. An
        # earlier version of this trap required a slash here on the
        # (mistaken) assumption that bare names were command_not_found's
        # job alone; verified directly that this trap fires for bare names
        # too, and command_not_found_handle was in fact removed from this
        # file entirely when it turned out to be insufficient for the
        # ./game.exe case -- leaving the slash guard here would have left
        # bare `game.exe` silently unhandled in bash specifically (zsh's
        # accept-line widget below has no such gap, since it never
        # required a slash).
        if _apex_win_is_exe_command "$BASH_COMMAND"; then
            _apex_win_in_dispatch=1
            _apex_win_dispatch_line "$BASH_COMMAND"
            unset _apex_win_in_dispatch
            return 1 # veto the original command (needs extdebug)
        fi
        return 0
    }
    trap '_apex_win_bash_debug_trap' DEBUG
fi
