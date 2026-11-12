# LIMSON — Claude Code 프로젝트 메모

한국투자증권 공식 open-trading-api 포크에 **전략 빌더 + LEAN 백테스터**를 확장한 프로젝트.
- 현재 원격: `https://github.com/zhanggu9/limson` (public). 예전 포크 `zhanggu9/open-trading-api`는 더 이상 쓰지 않는다.
- 원본(koreainvestment) 커밋 이력은 보존한다. history rewrite / force push 금지.
- 루트 README 상단 「추가한 기능」은 우리 작업, 아래 「원본 README」는 한국투자증권 원문 → 원문 부분은 수정하지 않는다.

## 구조
| 경로 | 역할 | 포트 |
| --- | --- | --- |
| `strategy_builder/` | 비주얼 전략 빌더 (FastAPI `backend.main:app` + Next.js) | BE 8000 / FE 3000 (`/builder`) |
| `backtester/` | LEAN 백테스터 (FastAPI `backend.main:app` + Next.js), 패키지 `kis_backtest` | BE 8002 (`/api/health`) / FE 3001 (`/backtest`) |
| `backtester/kis_backtest/providers/postgres/` | 자체 수집 OHLCV PostgreSQL 데이터 공급자 | — |
| `backtester/kis_backtest/lean/executor.py` | Docker LEAN 실행 (`subprocess.run`) | — |
| `backtester/kis_backtest/optimize/`, `backtester/backend/routes/optimize.py` | 하이퍼옵트 (Optuna TPE 탐색 공간·실행기·목표 점수, 스트리밍 API) | FE `/backtest/optimize` |
| `deploy/` | EC2 배포: `deploy.sh`(로컬→서버), `server_setup.sh`, nginx 템플릿, systemd 4개 | 공개 `https://limson.duckdns.org` (443) |
| `start.sh` (루트) | 4개 서비스 통합 실행 + 헬스체크 + 시그널 시 LEAN 컨테이너 정리 | — |
| `examples_llm/`, `examples_user/`, `MCP/`, `legacy/` | 원본 샘플 코드 — 특별한 이유 없으면 건드리지 않음 | — |

## 실행
- 로컬 전체 실행: `./start.sh` (Python은 `uv run`, 프론트는 `npm run dev`)
- 배포: `./deploy/deploy.sh` (기본 `DEPLOY_HOST=limson.duckdns.org`). 웹은 `https://limson.duckdns.org` (DuckDNS + Let's Encrypt).
  nginx는 서버 안에서 `WEB_PORT=8011`도 듣지만 보안 그룹에서 8011은 닫혀 있다 (헬스체크 전용). 8010은 서버의 pg-stock-api가 사용 중.
- 개발 환경: Windows + WSL Ubuntu 24.04, VS Code

## 핵심 설계 결정 (바꾸기 전에 사용자에게 확인)
- **LEAN 실행은 동기식**: 요청 핸들러 안에서 `subprocess.run`으로 컨테이너 종료까지 대기 ("Keep LEAN execution synchronous" 커밋). 그래서 nginx `/backtest/api/` 타임아웃을 900초로 둠. 비동기/큐 방식으로 바꾸지 말 것.
- **하이브리드 모드** (`BACKTEST_MODE=server|local`): EC2 t3.micro(1GB RAM)는 LEAN 이미지(압축 ~14GB, 디스크 50GB+ 필요)를 못 돌림 → 웹은 EC2, 백테스트 API+LEAN은 로컬 PC(`deploy/local_backtest_worker.sh`)에서 돌리고 SSH 역터널로 EC2 `127.0.0.1:8002`에 붙임. 끊기면 5초마다 재접속.
- **데이터 소스**: 백테스트 OHLCV는 KIS API가 아니라 PostgreSQL(`DATABASE_URL`, 테이블명 `PG_OHLCV_TABLE`)에서 읽음. 분봉도 PostgreSQL `PG_MINUTE_TABLE`(기본 `ohlcv_minute_day`, pg-stock-api가 KIS에서 매일 수집, 2025-10-10~)에서 읽고 1~60분봉으로 묶어 LEAN에 넣음. 테이블·컬럼 식별자는 검증해서 SQL 인젝션 방지 — 이 검증을 우회하는 f-string SQL 금지.
- **분봉 LEAN 성능**: 커스텀 분봉은 초당 약 3,500봉 + 기동 약 5초 (Python `Reader`/`OnData` 오버헤드라 파싱 최적화로는 안 빨라짐). 그래서 봉 예산 `MINUTE_BACKTEST_MAX_BARS`(150만)로 실행 시간을 nginx 900초 안에 묶음.
- **가격 기준 = KIS 수정주가** (2026-11-05~): 일봉·분봉 모두. pg-stock-api가 일봉을 KIS 수정주가로 받고(`OHLCV_SYNC_PROVIDER=kis`), 매일 분봉 수집 뒤 `price_adjust`로 분할·병합 등이 생긴 종목의 일봉을 다시 받고 예전 분봉에 수정계수를 곱한다. 바꾸기 전 값: 일봉 `ohlcv_before_kis_adjust`, 분봉 `ohlcv_minute_day_original`, 기록 `price_adjust_log`.
- **하이퍼옵트도 요청 하나 안에서 끝낸다** (2026-11-10~): `POST /api/optimize/run`이 진행 상황을 NDJSON으로 흘려보내며(5초마다 ping) 끝까지 돈다. 큐·백그라운드 작업 없음. 시행마다 LEAN은 그대로 `subprocess.run`으로 기다리고, 스레드로 최대 12개(`HYPEROPT_MAX_CONCURRENCY`, 화면 기본 10개) 동시에 돌린다 (2026-11-12 실측, 14코어·15GB: 분봉 4개 303초 → 12개 106초, 12개 동시에 메모리 약 5GB). 브라우저 연결이 끊기면 멈추고 컨테이너를 kill. 한 번에 하나만(409). nginx `/backtest/api/optimize/`는 버퍼링 끔·3600초. 데이터는 `.lean-workspace/hyperopt/<id>/data`에 한 번 준비하고(일반 백테스트와 분리) 끝나면 폴더째 지운다.
- 하이퍼옵트 결과는 같은 값의 일반 백테스트와 **똑같아야 한다**: 비용 기본값 규칙(`or`)과 분봉 워밍업(`minute_warmup_start`)을 일반 백테스트와 공유한다. 바꿀 때 둘 다 맞출 것.
- **LEAN `data-feed-workers-count` = 1** (`LEAN_DATA_FEED_WORKERS`): 기본값(코어 수)이면 LEAN 여러 개를 동시에 띄울 때 워밍업 시작 직후("Launching analysis ... Warmup: True") 교착으로 멈춘다 (실측 16개 중 9개 → 1로 두면 32개 중 0개, 결과·속도 동일).
- 하이브리드 모드에서 Docker가 꺼져 있어도 워커는 API를 띄운다 (백테스트 실행만 503).
- 불완전한 벤치마크(KOSPI) 곡선은 결과에서 제외한다.
- nginx 단일 포트 + Basic 인증. 인증 정보·DB URL·SSH 키는 커밋하지 않는다 (`kis_devlp.yaml`에 실제 키 넣지 말 것).

## 운영 메모
- 빌더의 「내 전략」은 서버가 아니라 브라우저 localStorage(`kis2_custom_strategies`)에 있다. 백테스터는 같은 주소(nginx 단일 포트)일 때만 그걸 읽어 「내 전략」으로 보여 준다 → 로컬 개발(3000/3001 포트가 다름)에서는 비어 보인다. 저장할 때 빌더가 `yaml` 필드를 같이 넣고, 빌더를 열 때마다 모든 전략의 YAML을 다시 만든다.
- 백테스트마다 `backtester/.lean-workspace/projects/bt_*` 폴더가 남고 자동 삭제되지 않음. 컨테이너가 root로 쓰므로 root 소유 → 주기적 정리 필요.
- 서버 실행 사용자는 `docker` 그룹 소속이어야 함 (`server_setup.sh`가 처리).

## 코드 스타일
- 문자열은 항상 큰따옴표(`"`). 작은따옴표 금지 (Python·TS·셸 모두, 셸은 의미가 달라지는 경우만 예외).
- 사용자 대상 메시지·로그·문서는 한국어.
- 커밋 메시지: `feat:`, `fix:`, `docs:` 등 conventional prefix.
