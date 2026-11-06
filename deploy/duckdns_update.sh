#!/usr/bin/env bash
# DuckDNS 주소(예: limson.duckdns.org)를 이 서버의 현재 공인 IP로 맞춘다.
# systemd 타이머(limson-duckdns.timer)가 5분마다 실행한다 — 고정 IP가 없어 서버를 껐다 켜면 IP가 바뀌기 때문.
# 토큰은 /etc/open-trading-api/duckdns.env(root 전용)에만 있고, 명령줄(ps)에 드러나지 않게 curl 설정으로 넘긴다.
set -euo pipefail

ENV_FILE="${DUCKDNS_ENV_FILE:-/etc/open-trading-api/duckdns.env}"
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${DUCKDNS_DOMAIN:?DUCKDNS_DOMAIN 없음}" "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN 없음}"

# ip= 를 비우면 DuckDNS가 요청을 보낸 공인 IP를 쓴다.
result="$(curl -fsS --max-time 20 -K - <<<"url = \"https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=\"")"
if [[ "$result" != "OK" ]]; then
  echo "DuckDNS 갱신 실패: ${result:-응답 없음} (도메인 이름·토큰 확인)" >&2
  exit 1
fi
echo "DuckDNS 갱신 OK: ${DUCKDNS_DOMAIN}.duckdns.org"
