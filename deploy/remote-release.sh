#!/usr/bin/env bash
# A transport disconnect must not kill a verified, scoped PLUS release.
set -Eeuo pipefail
umask 077
mode="${1:?mode required}"
revision="${2:?commit required}"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid commit' >&2; exit 1; }
root=/home/deploy/plus/releases/dispatch
[[ -d /home/deploy/plus && ! -L /home/deploy/plus/releases ]] || exit 1
mkdir -p "$root"
[[ ! -L "$root" ]] || exit 1
status="$root/$revision.exit"
pidfile="$root/$revision.pid"
log="$root/$revision.log"
report() {
  if [[ -f "$status" ]]; then
    code="$(cat "$status")"
    if [[ "$code" == 0 ]]; then echo succeeded; else echo "failed:$code"; fi
  elif [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then echo running
  else echo interrupted; fi
}
case "$mode" in
  --status) report ;;
  --start)
    archive="${3:?archive required}"
    [[ "$archive" == "/tmp/plus-release-$revision.tar.gz" && -f "$archive" && ! -L "$archive" ]] || exit 1
    exec 9> "$root/$revision.launch.lock"
    flock 9
    prior="$(report)"
    if [[ "$prior" == running || "$prior" == succeeded ]]; then echo "$prior"; exit 0; fi
    if [[ "$prior" == failed:* || -f "$pidfile" ]]; then
      echo 'Previous attempt requires inspection; choose a new verified release commit' >&2; exit 1
    fi
    nohup bash "$0" --run "$revision" "$archive" > "$log" 2>&1 < /dev/null 9>&- &
    process=$!
    printf '%s\n' "$process" > "$pidfile.tmp"
    mv "$pidfile.tmp" "$pidfile"
    echo running
    ;;
  --run)
    archive="${3:?archive required}"
    [[ "$archive" == "/tmp/plus-release-$revision.tar.gz" ]] || exit 1
    exec 8> "$root/$revision.run.lock"
    flock -n 8 || exit 75
    finish() {
      code=$?
      trap - EXIT
      printf '%s\n' "$code" > "$status.tmp"
      mv "$status.tmp" "$status"
      exit "$code"
    }
    trap finish EXIT
    bash "/tmp/plus-release-$revision.sh" "$revision" "$archive"
    ;;
  *) echo 'Unsupported mode' >&2; exit 1 ;;
esac
