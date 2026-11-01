#!/usr/bin/env bash
# Record which Keen pages run which database queries while you use Keen.
#
# Run it from the directory you start Keen from (where docker-compose.yml is):
#
#   bash tools/keen-perf-trace.sh [capture-name]
#
# Then use Keen in your browser. Type a note and press Enter at any time to
# mark what you are about to do ("open control A.5.17", "export audit ZIP").
# Press Enter on an empty line, or Ctrl-C, when you have finished. Run in the
# background, or with DURATION set, it stops on SIGINT or SIGTERM, when
# <capture-name>/stop exists, or after DURATION seconds.
#
# While it runs:
#   - PostgreSQL logs every statement with its duration, and the full plan
#     (EXPLAIN ANALYZE with buffers) of any statement slower than SLOW_MS.
#     Plans time each step, which adds a little to every statement's duration.
#   - keen-api and the workers run with KEEN_SQL_TRACE on, so each statement
#     names the API call and page, or the worker task, that ran it.
# Afterwards both are switched off again, also after Ctrl-C, a lost terminal or
# an error, and the capture is summarised in <capture-name>/report.txt and
# packed into <capture-name>.tar.gz. If the script is killed outright, run it
# again: it reports what an earlier trace left switched on and how to clear it.
#
# Settings (environment variables):
#   SLOW_MS=50          plan statements at least this slow, in milliseconds
#   DURATION=600        stop after this many seconds
#   KEEN_PG_USER=keen   PostgreSQL superuser inside the postgres service
#   KEEN_PG_DB=keen     Keen's database
#
# The API image must include KEEN_SQL_TRACE support; with an older image,
# queries are recorded without their pages.
#
# psycopg2 sends values inside the SQL text, so postgres.log, and the plans and
# error messages in report.txt, contain real identifiers and filter values.
# Check them before sharing; the statement summaries replace values with "?".
set -euo pipefail
# The capture holds values from the database: keep it private to this user.
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_PY="$HERE/keen_perf_report.py"
NAME="${1:-keen-perf-$(date -u +%Y%m%d-%H%M%S)}"
OUT="$PWD/$NAME"
SLOW_MS="${SLOW_MS:-50}"
DURATION="${DURATION:-}"
PG_USER="${KEEN_PG_USER:-keen}"
PG_DB="${KEEN_PG_DB:-keen}"
TRACEABLE=(keen-api keen-worker keen-http-worker keen-imap-worker)
OVERRIDE_NAME="keen-perf-trace-override.yml"
PG_SETTINGS=(session_preload_libraries log_min_duration_statement log_line_prefix
  log_timezone log_lock_waits log_temp_files track_io_timing
  auto_explain.log_min_duration auto_explain.log_analyze
  auto_explain.log_buffers auto_explain.log_settings)

# Messages must never stop the script: restoring runs after a lost terminal.
say() { printf '%s\n' "$*" >&2 2>/dev/null || true; }
die() {
  say "keen-perf-trace: $*"
  exit 1
}
now() {
  local stamp
  stamp="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
  case "$stamp" in *N*) date -u +%Y-%m-%dT%H:%M:%SZ ;; *) printf '%s\n' "$stamp" ;; esac
}

[ -f "$REPORT_PY" ] || die "keen_perf_report.py must sit next to this script ($HERE)."
[[ "$SLOW_MS" =~ ^[0-9]+$ ]] || die "SLOW_MS must be a whole number of milliseconds."
[ -z "$DURATION" ] || [[ "$DURATION" =~ ^[0-9]+$ ]] || die "DURATION must be a number of seconds."
[ ! -e "$OUT" ] || die "$OUT already exists; choose another capture name."

# Use the Compose project, files and environment the running containers came
# from, so recreating them keeps their configuration.
api_container="$(docker compose ps -q keen-api 2>/dev/null | head -n 1 || true)"
[ -n "$api_container" ] || die "keen-api is not running here. Run this from the directory you start Keen from."
label() {
  local value
  value="$(docker inspect -f "{{index .Config.Labels \"$1\"}}" "$api_container")"
  [ "$value" = "<no value>" ] && value=""
  printf '%s' "$value"
}
DC=(docker compose -p "$(label com.docker.compose.project)")
workdir="$(label com.docker.compose.project.working_dir)"
[ -z "$workdir" ] || DC+=(--project-directory "$workdir")
leftover=0
IFS=',' read -r -a compose_files <<<"$(label com.docker.compose.project.config_files)"
for file in "${compose_files[@]}"; do
  if [ "$(basename "$file")" = "$OVERRIDE_NAME" ]; then
    leftover=1
  elif [ -n "$file" ]; then
    DC+=(-f "$file")
  fi
done
IFS=',' read -r -a env_files <<<"$(label com.docker.compose.project.environment_file)"
for file in "${env_files[@]}"; do [ -z "$file" ] || DC+=(--env-file "$file"); done
# Services in a Compose profile (keen-imap-worker) exist for Compose only while
# their profile is active. Activating every profile changes nothing here: only
# the services named below are recreated.
while read -r profile; do
  [ -z "$profile" ] || DC+=(--profile "$profile")
done < <("${DC[@]}" config --profiles 2>/dev/null || true)

TRACED=()
while read -r service; do
  for candidate in "${TRACEABLE[@]}"; do
    [ "$service" = "$candidate" ] && TRACED+=("$service")
  done
done < <("${DC[@]}" ps --services --status running)
"${DC[@]}" ps --services --status running | grep -qx postgres || die "the postgres service is not running."

if [ "$leftover" = 1 ]; then
  die "an earlier trace ended before switching KEEN_SQL_TRACE off. Switch it off with:
  $(printf '%q ' "${DC[@]}" up -d --no-deps --no-build "${TRACED[@]}")
then run this again."
fi

# Recreating a container applies the current Compose configuration and image.
# Stop if either differs from what is running (an edited .env, a changed
# Compose file, or a newer build under the same tag with its own migrations).
hashes="$("${DC[@]}" config --hash "$(IFS=,; printf '%s' "${TRACED[*]}")")" ||
  die "could not read the Compose configuration for ${TRACED[*]} (see above)."
while read -r service hash; do
  container="$("${DC[@]}" ps -q "$service" | head -n 1)" ||
    die "could not find the $service container (see above)."
  running="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$container")"
  [ "$running" = "$hash" ] ||
    die "$service is running with a different configuration from the current Compose files. Start Keen the usual way first, then retry."
  image="$(docker inspect -f '{{.Config.Image}}' "$container")"
  [ "$(docker inspect -f '{{.Image}}' "$container")" = "$(docker image inspect -f '{{.Id}}' "$image" 2>/dev/null || true)" ] ||
    die "$service is running a different build from the one $image names now. Start Keen the usual way first, then retry."
done <<<"$hashes"

PSQL=("${DC[@]}" exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -X -q -v ON_ERROR_STOP=1)
[ "$("${PSQL[@]}" -Atc "SELECT rolsuper FROM pg_roles WHERE rolname = current_user")" = t ] ||
  die "$PG_USER is not a PostgreSQL superuser, so statement logging cannot be switched on."
setting_list="'$(IFS=,; printf '%s' "${PG_SETTINGS[*]}" | sed "s/,/','/g")'"

TMP="$(mktemp -d)"
OVERRIDE="$TMP/$OVERRIDE_NAME"
{
  echo "services:"
  for service in "${TRACED[@]}"; do
    printf '  %s:\n    environment:\n      KEEN_SQL_TRACE: "true"\n' "$service"
  done
} >"$OVERRIDE"

table_stats() {
  "${PSQL[@]}" -At -F, -c "/* keen-perf-trace */ SELECT relname, seq_scan, seq_tup_read, coalesce(idx_scan, 0),
    coalesce(idx_tup_fetch, 0), n_live_tup FROM pg_stat_user_tables ORDER BY relname"
}

# Recreate the traced services, showing Compose's output only if it fails.
compose_up() {
  if ! "${DC[@]}" "$@" up -d --no-deps --no-build "${TRACED[@]}" >"$TMP/up.log" 2>&1; then
    cat "$TMP/up.log" >&2 2>/dev/null || true
    return 1
  fi
}

wait_for_api() {
  local deadline=$((SECONDS + 120))
  while [ "$SECONDS" -lt "$deadline" ]; do
    "${DC[@]}" exec -T keen-api curl -fsS -o /dev/null http://127.0.0.1:8080/health 2>/dev/null && return 0
    sleep 1
  done
  say "keen-api did not report healthy within two minutes; check 'docker compose logs keen-api'."
  return 1
}

saved_settings=""
pg_changed=0
services_changed=0
restore() {
  # Finish restoring even if Ctrl-C is pressed again; Compose inherits this.
  trap '' INT TERM HUP
  if [ "$pg_changed" = 1 ]; then
    say "Switching statement logging off..."
    {
      for setting in "${PG_SETTINGS[@]}"; do echo "ALTER SYSTEM RESET $setting;"; done
      printf '%s\n' "$saved_settings"
      echo "SELECT pg_reload_conf();"
    } | "${PSQL[@]}" >/dev/null ||
      say "WARNING: could not reset PostgreSQL settings. Run ALTER SYSTEM RESET for: ${PG_SETTINGS[*]}"
    pg_changed=0
  fi
  if [ "$services_changed" = 1 ]; then
    say "Recreating ${TRACED[*]} with KEEN_SQL_TRACE off..."
    if compose_up; then
      wait_for_api || true
    else
      say "WARNING: could not recreate ${TRACED[*]}; start them the usual way to switch KEEN_SQL_TRACE off."
    fi
    services_changed=0
  fi
}
cleanup() {
  local status=$?
  set +e
  restore
  rm -rf "$TMP"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
trap 'exit 129' HUP

mkdir -p "$OUT"
say "Recording database details..."
"${PSQL[@]}" >"$OUT/database.txt" <<'SQL'
\echo '== Server and planner settings'
SELECT version();
SELECT name, setting, unit, source FROM pg_settings
 WHERE name IN ('shared_buffers', 'work_mem', 'effective_cache_size', 'maintenance_work_mem',
                'random_page_cost', 'effective_io_concurrency', 'max_parallel_workers_per_gather',
                'jit', 'default_statistics_target', 'max_connections')
 ORDER BY name;
\echo '== Largest tables'
SELECT c.relname, c.reltuples::bigint AS est_rows,
       pg_size_pretty(pg_table_size(c.oid)) AS table_size,
       pg_size_pretty(pg_indexes_size(c.oid)) AS index_size,
       s.n_live_tup, s.n_dead_tup, s.last_analyze, s.last_autoanalyze
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
  LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
 WHERE c.relkind = 'r'
 ORDER BY pg_total_relation_size(c.oid) DESC
 LIMIT 25;
\echo '== Indexes on the 15 largest tables'
SELECT i.tablename, i.indexname, s.idx_scan, i.indexdef
  FROM pg_indexes i
  JOIN pg_stat_user_indexes s ON s.indexrelname = i.indexname AND s.schemaname = i.schemaname
 WHERE i.schemaname = 'public'
   AND i.tablename IN (SELECT relname FROM pg_stat_user_tables
                        ORDER BY pg_total_relation_size(relid) DESC LIMIT 15)
 ORDER BY i.tablename, i.indexname;
SQL

# Keep any of these settings made earlier with ALTER SYSTEM, to put them back
# afterwards, unless they are what an earlier trace left behind.
earlier_trace="$("${PSQL[@]}" -At -c "SELECT count(*) = 2 FROM pg_file_settings
  WHERE sourcefile LIKE '%postgresql.auto.conf' AND error IS NULL
    AND ((name = 'session_preload_libraries' AND setting = 'auto_explain')
      OR (name = 'log_min_duration_statement' AND setting = '0'))")"
if [ "$earlier_trace" = t ]; then
  say "Note: statement logging was still on from an earlier trace; it is switched off when this one ends."
else
  saved_settings="$("${PSQL[@]}" -At -c "SELECT format('ALTER SYSTEM SET %s = %L;', name, setting)
    FROM pg_file_settings
    WHERE sourcefile LIKE '%postgresql.auto.conf' AND error IS NULL AND name IN ($setting_list)
    ORDER BY seqno")"
  [ -z "$saved_settings" ] ||
    say "Note: keeping earlier ALTER SYSTEM values for these settings, to put back afterwards:
$saved_settings"
fi
table_stats >"$OUT/tables-before.csv"

say "Switching statement logging on (plans for statements of ${SLOW_MS} ms or more)..."
pg_changed=1
"${PSQL[@]}" >/dev/null <<SQL
LOAD 'auto_explain';
ALTER SYSTEM SET session_preload_libraries = 'auto_explain';
ALTER SYSTEM SET log_min_duration_statement = 0;
ALTER SYSTEM SET log_line_prefix = '%m [%p] ';
ALTER SYSTEM SET log_timezone = 'UTC';
ALTER SYSTEM SET log_lock_waits = on;
ALTER SYSTEM SET log_temp_files = 0;
ALTER SYSTEM SET track_io_timing = on;
ALTER SYSTEM SET auto_explain.log_min_duration = '${SLOW_MS}ms';
ALTER SYSTEM SET auto_explain.log_analyze = on;
ALTER SYSTEM SET auto_explain.log_buffers = on;
ALTER SYSTEM SET auto_explain.log_settings = on;
SELECT pg_reload_conf();
SQL

say "Recreating ${TRACED[*]} with KEEN_SQL_TRACE on..."
services_changed=1
compose_up -f "$OVERRIDE" || die "could not recreate ${TRACED[*]}."
wait_for_api
"${DC[@]}" exec -T keen-api test -f /app/app/db/sql_trace.py ||
  say "WARNING: this keen-api image predates KEEN_SQL_TRACE. Queries are recorded without their pages; rebuild the image for page attribution."

# Take notes only in the foreground of a terminal; a background job that
# reads from the terminal would be stopped.
interactive=0
if [ -z "$DURATION" ] && [ -t 0 ]; then
  foreground="$(ps -o tpgid= -p $$ 2>/dev/null | tr -d ' ' || true)"
  group="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ' || true)"
  [ -n "$group" ] && [ "$foreground" = "$group" ] && interactive=1
fi
START="$(now)"
say ""
say "  Ready: tracing since $START. Use Keen in your browser now."
if [ "$interactive" = 1 ]; then
  say "  Type a note and press Enter to mark what you are about to do."
  say "  Press Enter on an empty line, or Ctrl-C, when you have finished."
else
  say "  Stop with: kill -INT $$   (or: touch $OUT/stop)"
  say "  The trace stops by itself after ${DURATION:-3600} seconds."
fi
say ""

: >"$OUT/notes.tsv"
stop=0
trap 'stop=1' INT TERM HUP
if [ "$interactive" = 0 ]; then
  deadline=$((SECONDS + ${DURATION:-3600}))
  while [ "$stop" = 0 ] && [ ! -e "$OUT/stop" ] && [ "$SECONDS" -lt "$deadline" ]; do sleep 1; done
  rm -f "$OUT/stop"
else
  while [ "$stop" = 0 ]; do
    IFS= read -r -p "note> " note || break
    [ -n "$note" ] || break
    printf '%s\t%s\n' "$(now)" "$note" >>"$OUT/notes.tsv"
  done
fi
trap 'exit 130' INT TERM
trap 'exit 129' HUP
END="$(now)"
say ""
say "Collecting the capture..."
sleep 2
table_stats >"$OUT/tables-after.csv"
"${DC[@]}" logs --no-color --no-log-prefix --since "$START" postgres >"$OUT/postgres.log" 2>&1
"${DC[@]}" logs --no-color --no-log-prefix --since "$START" "${TRACED[@]}" 2>&1 |
  grep -a 'KEEN_SQL_TRACE {' | sed 's/^.*KEEN_SQL_TRACE //' >"$OUT/trace.jsonl" || true
printf 'start\t%s\nend\t%s\nslow_ms\t%s\ntraced\t%s\n' "$START" "$END" "$SLOW_MS" "${TRACED[*]}" >"$OUT/capture.tsv"

restore
trap 'exit 130' INT TERM
trap 'exit 129' HUP

summarise() {
  local status
  if command -v python3 >/dev/null 2>&1; then
    python3 "$REPORT_PY" "$OUT"
    return
  fi
  # No Python on this host: the API image has it. The copy holds real values,
  # so it is removed whether or not the summary works.
  "${DC[@]}" exec -T keen-api rm -rf /tmp/keen-perf
  "${DC[@]}" cp "$OUT" keen-api:/tmp/keen-perf >/dev/null &&
    "${DC[@]}" cp "$REPORT_PY" keen-api:/tmp/keen-perf/keen_perf_report.py >/dev/null &&
    "${DC[@]}" exec -T keen-api python /tmp/keen-perf/keen_perf_report.py /tmp/keen-perf
  status=$?
  "${DC[@]}" exec -T keen-api rm -rf /tmp/keen-perf || say "WARNING: remove /tmp/keen-perf from keen-api by hand."
  return "$status"
}
say "Summarising..."
summarise >"$OUT/report.txt" ||
  say "WARNING: the summary failed; the raw capture is still packed for sending."
tar -czf "$OUT.tar.gz" -C "$(dirname "$OUT")" "$(basename "$OUT")"

say ""
say "Done."
say "  Summary:  $OUT/report.txt"
say "  Capture:  $OUT.tar.gz (the summary plus the raw logs it came from)"
say "Send the .tar.gz to look into the slow pages together. postgres.log, and the"
say "plans and errors in report.txt, contain real values from your data; check"
say "them first."
