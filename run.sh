#!/usr/bin/env bash
# AI 펀드매니저(모의투자) 실행 스크립트
#   ./run.sh          Docker 가 켜져 있으면 docker compose, 아니면 로컬(uv + npm)로 실행
#   ./run.sh docker   docker compose 로 실행 (백엔드 + 프론트 + PostgreSQL)
#   ./run.sh local    로컬 실행 (SQLite)
#   ./run.sh test     백엔드 + 프론트 테스트
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-auto}"
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
say() { printf '%s\n' "${GREEN}▶${RESET} $*"; }
warn() { printf '%s\n' "${YELLOW}!${RESET} $*"; }
die() { printf '%s\n' "${RED}✗ $*${RESET}" >&2; exit 1; }

# ---------------------------------------------------------------- .env 준비
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  warn ".env 가 없어서 .env.example 로 만들었어요. 키 없이도 가상 시장(MOCK)으로 바로 실행됩니다."
  warn "LLM·실데이터를 쓰려면 .env 의 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 등을 채운 뒤 다시 실행하세요."
fi

# 안전 확인: TRADING_MODE 는 paper 만 허용
tm="$(grep -E '^[[:space:]]*TRADING_MODE[[:space:]]*=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'" || true)"
if [[ -n "$tm" && "$tm" != "paper" ]]; then
  die "TRADING_MODE=$tm — 이 시스템은 모의투자(paper) 전용입니다. .env 에서 TRADING_MODE=paper 로 바꾸세요."
fi

filled() { grep -qE "^[[:space:]]*$1[[:space:]]*=[[:space:]]*[^[:space:]#]+" .env; }
echo
printf '%s\n' "${BOLD}🦉 AI 펀드매니저 — 모의투자(PAPER)${RESET}"
if filled LLM_BASE_URL && filled LLM_MODEL && filled LLM_API_KEY; then say "LLM: 설정됨"; else warn "LLM: 미설정 → 규칙 기반 매니저"; fi
if filled DATA_GO_KR_SERVICE_KEY; then say "시세: 공공데이터포털(실데이터, T+1)"; else warn "시세: 가상 시장(MOCK) + 가속 시뮬레이션"; fi
filled DART_API_KEY && say "재무·공시: OpenDART" || true
filled NAVER_CLIENT_ID && say "뉴스: 네이버 검색" || true
echo

docker_ok() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

run_docker() {
  docker_ok || die "Docker 데몬에 연결할 수 없어요. Docker Desktop 을 켜거나 './run.sh local' 로 실행하세요."
  say "docker compose 로 기동합니다 (처음엔 이미지 빌드로 몇 분 걸려요)"
  say "준비되면 브라우저에서 http://localhost:5173 을 여세요. 종료: Ctrl+C"
  exec docker compose up --build
}

run_local() {
  command -v uv >/dev/null 2>&1 || die "uv 가 필요해요: https://docs.astral.sh/uv/ (또는 Docker 로 실행)"
  command -v npm >/dev/null 2>&1 || die "Node.js(npm) 가 필요해요: https://nodejs.org (또는 Docker 로 실행)"
  say "백엔드 의존성 설치 (uv)"
  (cd backend && uv sync --frozen --python 3.12 >/dev/null)
  if [[ ! -d frontend/node_modules ]]; then
    say "프론트엔드 의존성 설치 (npm ci)"
    (cd frontend && npm ci --no-audit --no-fund >/dev/null)
  fi
  for port in 8000 5173; do
    if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      die "포트 $port 를 이미 쓰는 프로세스가 있어요. 종료 후 다시 실행하세요."
    fi
  done

  pids=()
  cleanup() {
    echo
    say "종료 중…"
    for p in "${pids[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done
    wait 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  say "백엔드 기동 → http://127.0.0.1:8000 (API 문서 /api/docs)"
  (cd backend && exec uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000) &
  pids+=($!)
  for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 || die "백엔드가 뜨지 않았어요. 위 로그를 확인하세요."

  say "프론트엔드 기동 → http://localhost:5173"
  (cd frontend && exec npx vite --host 127.0.0.1 --port 5173 --strictPort) &
  pids+=($!)
  sleep 2
  printf '\n%s\n\n' "${BOLD}✅ 준비 완료: http://localhost:5173  (처음이면 로컬 계정을 만드세요 · 종료: Ctrl+C)${RESET}"
  wait
}

run_tests() {
  (cd backend && uv sync --frozen --python 3.12 >/dev/null && uv run pytest -q --cov=app --cov-report=term-missing:skip-covered)
  (cd frontend && { [[ -d node_modules ]] || npm ci --no-audit --no-fund >/dev/null; } && npm test)
}

case "$MODE" in
  docker) run_docker ;;
  local) run_local ;;
  test) run_tests ;;
  auto) if docker_ok; then run_docker; else warn "Docker 가 꺼져 있어 로컬 모드로 실행합니다"; run_local; fi ;;
  *) die "사용법: ./run.sh [auto|docker|local|test]" ;;
esac
