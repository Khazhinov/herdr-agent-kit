#!/usr/bin/env bash
# Tests plus a privacy scan of tracked and staged files.
set -euo pipefail
cd "$(dirname "$0")/.."
for t in tests/test_tool_ask.py tests/test_limits.py; do python3 "$t" >/dev/null 2>&1 || { python3 "$t"; exit 1; }; done
pat='/home/[a-z]|/Users/[A-Za-z]|/mnt/[a-z]/|C:\\Users\\'
[ -f local/private-patterns.txt ] && while IFS= read -r p; do [ -n "$p" ] && pat="$pat|$p"; done < local/private-patterns.txt
files=$(git ls-files -co --exclude-standard | grep -v '^scripts/self-check.sh$' || true)
if [ -n "$files" ] && hits=$(echo "$files" | xargs grep -nIE "$pat" 2>/dev/null); then
  echo "private data in public files:"; echo "$hits"; exit 1
fi
if [ -n "$files" ] && hits=$(echo "$files" | grep '\.md$' | xargs grep -nP '\x{2014}|\x{2192}' 2>/dev/null); then
  echo "forbidden typography (U+2014, U+2192):"; echo "$hits"; exit 1
fi
echo PASS
