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
| `deploy/` | EC2 배포: `deploy.sh`(로컬→서버), `server_setup.sh`, nginx 템플릿, systemd 4개 | 공개 8011 |
| `start.sh` (루트) | 4개 서비스 통합 실행 + 헬스체크 + 시그널 시 LEAN 컨테이너 정리 | — |
| `examples_llm/`, `examples_user/`, `MCP/`, `legacy/` | 원본 샘플 코드 — 특별한 이유 없으면 건드리지 않음 | — |

## 실행
- 로컬 전체 실행: `./start.sh` (Python은 `uv run`, 프론트는 `npm run dev`)
- 배포: `./deploy/deploy.sh` (기본 `DEPLOY_HOST=13.209.87.155`, `WEB_PORT=8011` — 8010은 서버의 pg-stock-api가 사용 중)
- 개발 환경: Windows + WSL Ubuntu 24.04, VS Code

## 핵심 설계 결정 (바꾸기 전에 사용자에게 확인)
- **LEAN 실행은 동기식**: 요청 핸들러 안에서 `subprocess.run`으로 컨테이너 종료까지 대기 ("Keep LEAN execution synchronous" 커밋). 그래서 nginx `/backtest/api/` 타임아웃을 900초로 둠. 비동기/큐 방식으로 바꾸지 말 것.
- **하이브리드 모드** (`BACKTEST_MODE=server|local`): EC2 t3.micro(1GB RAM)는 LEAN 이미지(압축 ~14GB, 디스크 50GB+ 필요)를 못 돌림 → 웹은 EC2, 백테스트 API+LEAN은 로컬 PC(`deploy/local_backtest_worker.sh`)에서 돌리고 SSH 역터널로 EC2 `127.0.0.1:8002`에 붙임. 끊기면 5초마다 재접속.
- **데이터 소스**: 백테스트 OHLCV는 KIS API가 아니라 PostgreSQL(`DATABASE_URL`, 테이블명 `PG_OHLCV_TABLE`)에서 읽음. 테이블·컬럼 식별자는 검증해서 SQL 인젝션 방지 — 이 검증을 우회하는 f-string SQL 금지.
- 불완전한 벤치마크(KOSPI) 곡선은 결과에서 제외한다.
- nginx 단일 포트 + Basic 인증. 인증 정보·DB URL·SSH 키는 커밋하지 않는다 (`kis_devlp.yaml`에 실제 키 넣지 말 것).

## 운영 메모
- 백테스트마다 `backtester/.lean-workspace/projects/bt_*` 폴더가 남고 자동 삭제되지 않음. 컨테이너가 root로 쓰므로 root 소유 → 주기적 정리 필요.
- 서버 실행 사용자는 `docker` 그룹 소속이어야 함 (`server_setup.sh`가 처리).

## 코드 스타일
- 문자열은 항상 큰따옴표(`"`). 작은따옴표 금지 (Python·TS·셸 모두, 셸은 의미가 달라지는 경우만 예외).
- 사용자 대상 메시지·로그·문서는 한국어.
- 커밋 메시지: `feat:`, `fix:`, `docs:` 등 conventional prefix.
