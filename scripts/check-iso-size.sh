#!/usr/bin/env bash
# Axon OS — check an ISO against its size budget.
#
# Usage: scripts/check-iso-size.sh --edition standard|nvidia [options] ISO
#
#   --packages FILE   installed-size list written by build/build.sh (size_kb, package, version)
#   --previous FILE   the same list from the last good build, to show what changed
#   --top N           how many of the biggest packages to print (default 30)
#
# Exit codes: 0 within the limit (a warning is printed above the target), 1 over the
# limit, 2 bad usage or no ISO. Budgets come from build/size-budget.conf.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUDGET="${AXON_SIZE_BUDGET:-${HERE}/../build/size-budget.conf}"

edition="" packages="" previous="" top=30 iso=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --edition) edition="${2:-}"; shift 2 ;;
        --edition=*) edition="${1#*=}"; shift ;;
        --packages) packages="${2:-}"; shift 2 ;;
        --previous) previous="${2:-}"; shift 2 ;;
        --top) top="${2:-}"; shift 2 ;;
        -h | --help) sed -n '2,13p' "$0"; exit 0 ;;
        -*) echo "check-iso-size: unknown option $1" >&2; exit 2 ;;
        *) iso="$1"; shift ;;
    esac
done

case "${edition}" in
    standard | nvidia) ;;
    *) echo "check-iso-size: --edition must be standard or nvidia" >&2; exit 2 ;;
esac
[[ "${top}" =~ ^[0-9]+$ ]] || { echo "check-iso-size: --top needs a number" >&2; exit 2; }
[[ -f "${iso}" ]] || { echo "check-iso-size: ISO not found: ${iso:-<none>}" >&2; exit 2; }
[[ -f "${BUDGET}" ]] || { echo "check-iso-size: budget file not found: ${BUDGET}" >&2; exit 2; }
# shellcheck source=/dev/null
source "${BUDGET}"

upper="$(echo "${edition}" | tr '[:lower:]' '[:upper:]')"
target_var="${upper}_TARGET_MB"
limit_var="${upper}_LIMIT_MB"
target_mb="${!target_var}"
limit_mb="${!limit_var}"
reason_mb="${ADDED_MB_NEEDS_REASON:-50}"

bytes="$(stat -c %s "${iso}")"
mb=$((bytes / 1000000))

echo "== ISO size: ${edition} edition =="
printf '%s: %d MB (%s bytes)\n' "$(basename "${iso}")" "${mb}" "${bytes}"
printf 'target %d MB, hard limit %d MB\n' "${target_mb}" "${limit_mb}"

status=0
if ((mb > limit_mb)); then
    echo "FAIL: ${mb} MB is over the ${limit_mb} MB limit by $((mb - limit_mb)) MB"
    status=1
elif ((mb > target_mb)); then
    echo "WARNING: ${mb} MB is over the ${target_mb} MB target by $((mb - target_mb)) MB (limit ${limit_mb} MB)"
else
    echo "OK: $((target_mb - mb)) MB under the target"
fi

if [[ -n "${packages}" && -f "${packages}" ]]; then
    echo
    echo "== ${top} biggest installed packages =="
    # size_kb <TAB> package <TAB> version; lines starting with # are metadata
    grep -v '^#' "${packages}" | sort -t$'\t' -k1,1nr | head -n "${top}" |
        awk -F'\t' '{ printf "%8.1f MB  %s %s\n", $1 / 1024, $2, $3 }'
    total_kb="$(grep -v '^#' "${packages}" | awk -F'\t' '{ s += $1 } END { print s + 0 }')"
    printf '%8.1f MB  installed in total (%d packages)\n' \
        "$(awk -v k="${total_kb}" 'BEGIN { print k / 1024 }')" \
        "$(grep -vc '^#' "${packages}")"

    if [[ -n "${previous}" && -f "${previous}" ]]; then
        echo
        echo "== Changes since the previous build =="
        awk -F'\t' -v reason="${reason_mb}" '
            /^#/ { next }
            FNR == NR { old[$2] = $1; oldv[$2] = $3; next }
            { new[$2] = $1; newv[$2] = $3 }
            END {
                for (p in new) {
                    if (!(p in old)) {
                        printf "  added    %-40s %+8.1f MB\n", p, new[p] / 1024
                        added += new[p]
                    } else if (new[p] - old[p] > 512 || old[p] - new[p] > 512) {
                        printf "  changed  %-40s %+8.1f MB  (%s -> %s)\n", p, (new[p] - old[p]) / 1024, oldv[p], newv[p]
                        changed += new[p] - old[p]
                    }
                }
                for (p in old) if (!(p in new)) {
                    printf "  removed  %-40s %+8.1f MB\n", p, -old[p] / 1024
                    removed += old[p]
                }
                printf "  installed size: %+.1f MB (added %.1f, changed %+.1f, removed %.1f)\n", \
                    (added + changed - removed) / 1024, added / 1024, changed / 1024, removed / 1024
                if ((added + changed - removed) / 1024 > reason)
                    printf "  NOTE: more than %d MB added; the pull request must say why and which rule (A, B or C) allows it\n", reason
            }' "${previous}" "${packages}" | sort
        prev_bytes="$(sed -n 's/^# iso_bytes=\([0-9]*\).*/\1/p' "${previous}" | head -1)"
        if [[ -n "${prev_bytes}" ]]; then
            printf '  ISO size: %+d MB (was %d MB)\n' "$(((bytes - prev_bytes) / 1000000))" "$((prev_bytes / 1000000))"
        fi
    fi
fi

exit "${status}"
