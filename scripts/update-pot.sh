#!/bin/sh
# Refresh po/axon-os.pot from the strings marked for translation:
# _() and N_() in the Python apps and services, _t() in the Shell extension.
#
# Usage: scripts/update-pot.sh          rewrite po/axon-os.pot
#        scripts/update-pot.sh --check  exit 1 if the template is out of date
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
POT="$ROOT/po/axon-os.pot"
cd "$ROOT"

command -v xgettext >/dev/null || { echo "xgettext not found (apt install gettext)" >&2; exit 2; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git ls-files 'apps/*.py' 'services/*.py' | sort > "$TMP/py.txt"
git ls-files 'shell/axon-shell/*.js' | sort > "$TMP/js.txt"

common="--from-code=UTF-8 --add-comments=Translators: --add-location=file --sort-by-file
        --package-name=axon-os
        --msgid-bugs-address=https://github.com/ProjectDEV-git/Axon-OS/issues"
# shellcheck disable=SC2086
xgettext $common --language=Python --keyword=_ --keyword=N_ \
    --files-from="$TMP/py.txt" --output="$TMP/axon-os.pot"
# --join-existing adds the Shell strings and merges references to shared msgids.
# shellcheck disable=SC2086
xgettext $common --language=JavaScript --keyword=_t --join-existing \
    --files-from="$TMP/js.txt" --output="$TMP/axon-os.pot"
# A charset the template can be compiled with.
sed -i 's/charset=CHARSET/charset=UTF-8/' "$TMP/axon-os.pot"

# The creation date changes on every run, so leave it out of the comparison.
strip_date() { grep -v '^"POT-Creation-Date:' "$1"; }

if [ "${1:-}" = "--check" ]; then
    strip_date "$POT" > "$TMP/old" 2>/dev/null || { echo "po/axon-os.pot is missing" >&2; exit 1; }
    if strip_date "$TMP/axon-os.pot" | cmp -s - "$TMP/old"; then
        echo "po/axon-os.pot is up to date"
    else
        echo "po/axon-os.pot is out of date; run scripts/update-pot.sh" >&2
        exit 1
    fi
else
    cp "$TMP/axon-os.pot" "$POT"
    echo "wrote po/axon-os.pot ($(grep -c '^msgid ' "$POT") strings)"
fi
