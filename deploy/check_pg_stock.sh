#!/usr/bin/env bash
# pg-stock(PostgreSQL)에 어떤 데이터가 얼마나 쌓였는지, 분봉(장중 시각) 데이터가 있는지 읽기 전용으로 확인한다.
# 로컬 PC(WSL)에서 실행: SSH로 EC2에 접속해 pg-stock 컨테이너의 psql로 조회한다. 아무것도 수정하지 않는다.
#
#   ./deploy/check_pg_stock.sh
#
# 환경변수: DEPLOY_HOST(13.209.87.155) SSH_KEY(키 경로) KEY_DIR(/home/l/stock-coin-trade) SSH_USER(ubuntu)
#           PG_CONTAINER(pg-stock) PG_USER(admin) PG_DB(admin)
set -Eeuo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-13.209.87.155}"
KEY_DIR="${KEY_DIR:-/home/l/stock-coin-trade}"
SSH_USER="${SSH_USER:-ubuntu}"
PG_CONTAINER="${PG_CONTAINER:-pg-stock}"
PG_USER="${PG_USER:-admin}"
PG_DB="${PG_DB:-admin}"

if [[ -z "${SSH_KEY:-}" ]]; then
  SSH_KEY="$(find "$KEY_DIR" -maxdepth 1 -type f \( -name '*.pem' -o -name '*.key' \) 2>/dev/null | sort | head -n 1)"
fi
[[ -n "$SSH_KEY" && -f "$SSH_KEY" ]] || { echo "SSH 키를 찾지 못했습니다 (SSH_KEY=경로)." >&2; exit 1; }
TMP_KEY="$(mktemp)"; trap 'rm -f "$TMP_KEY"' EXIT
install -m 600 "$SSH_KEY" "$TMP_KEY"

read -r -d '' SQL <<'SQL' || true
\pset footer off
\echo '== 1. 테이블 목록과 크기 =='
SELECT n.nspname AS schema, c.relname AS "table",
       CASE WHEN c.reltuples < 0 THEN '통계 없음' ELSE to_char(c.reltuples::bigint, 'FM999,999,999,999') END AS "행 수(추정)",
       pg_size_pretty(pg_total_relation_size(c.oid)) AS "크기"
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(c.oid) DESC;

\echo '== 2. 날짜/시각 컬럼 (date = 일 단위, timestamp/time = 시각까지 저장 가능) =='
SELECT table_schema AS schema, table_name AS "table", column_name AS "column", data_type
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
  AND (data_type LIKE 'timestamp%' OR data_type LIKE 'time%' OR data_type = 'date')
ORDER BY 1, 2, 3;

\echo '== 3. public.ohlcv 요약 (일봉 테이블) =='
SELECT to_char(count(*), 'FM999,999,999') AS "행 수",
       count(DISTINCT ticker_code) AS "종목 수",
       min(trade_date) AS "첫 날짜", max(trade_date) AS "마지막 날짜"
FROM public.ohlcv;
SELECT max(cnt) AS "종목·날짜당 최대 행 수 (1이면 하루 1봉 = 일봉)"
FROM (SELECT count(*) AS cnt FROM public.ohlcv GROUP BY ticker_code, trade_date) s;

\echo '== 4. 시각 컬럼이 있는 테이블에 장중(00:00 이외) 데이터가 있는지 = 분봉 여부 =='
DO $$
DECLARE r record; total bigint; intraday bigint; tickers bigint; first_t text; last_t text;
BEGIN
  FOR r IN
    SELECT table_schema, table_name, column_name
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      AND data_type LIKE 'timestamp%'
  LOOP
    EXECUTE format(
      'SELECT count(*), count(*) FILTER (WHERE %1$I::time <> ''00:00''), min(%1$I)::text, max(%1$I)::text FROM %2$I.%3$I',
      r.column_name, r.table_schema, r.table_name)
      INTO total, intraday, first_t, last_t;
    RAISE NOTICE '%.%.% : 전체 % 행, 장중 시각 % 행, 기간 % ~ %',
      r.table_schema, r.table_name, r.column_name, total, intraday, first_t, last_t;
  END LOOP;
END $$;
SQL

echo "[check] $SSH_USER@$DEPLOY_HOST → docker exec $PG_CONTAINER psql -U $PG_USER -d $PG_DB (읽기 전용 조회)"
printf '%s\n' "$SQL" | ssh -i "$TMP_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
  "$SSH_USER@$DEPLOY_HOST" \
  "sudo docker exec -i $(printf '%q' "$PG_CONTAINER") psql -U $(printf '%q' "$PG_USER") -d $(printf '%q' "$PG_DB") -v ON_ERROR_STOP=0 -X -q"
