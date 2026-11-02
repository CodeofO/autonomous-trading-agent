#!/usr/bin/env bash
# AI 펀드매니저(모의투자) 실행 스크립트
#   ./run.sh          Docker 가 켜져 있으면 docker compose, 아니면 로컬(uv + npm)로 실행
#   ./run.sh docker   docker compose 로 실행 (백엔드 + 프론트 + PostgreSQL)
#   ./run.sh local    로컬 실행 (SQLite)
#   ./run.sh up       docker compose 를 백그라운드로 실행 (터미널을 닫아도 유지)
#   ./run.sh down     백그라운드 실행 중지
#   ./run.sh test     백엔드 + 프론트 테스트
# 포트: .env 의 FRONTEND_PORT(기본 15173) / BACKEND_PORT(기본 18000)
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

env_val() { grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'" || true; }
FRONTEND_PORT="${FRONTEND_PORT:-$(env_val FRONTEND_PORT)}"; FRONTEND_PORT="${FRONTEND_PORT:-15173}"
BACKEND_PORT="${BACKEND_PORT:-$(env_val BACKEND_PORT)}"; BACKEND_PORT="${BACKEND_PORT:-18000}"
export FRONTEND_PORT BACKEND_PORT
URL="http://localhost:${FRONTEND_PORT}"

filled() { grep -qE "^[[:space:]]*$1[[:space:]]*=[[:space:]]*[^[:space:]#]+" .env; }
echo
printf '%s\n' "${BOLD}🦉 AI 펀드매니저 — 모의투자(PAPER)${RESET}"
if filled LLM_BASE_URL && filled LLM_MODEL && filled LLM_API_KEY; then say "LLM: 설정됨"; else warn "LLM: 미설정 → 규칙 기반 매니저"; fi
if filled DATA_GO_KR_SERVICE_KEY; then say "시세: 공공데이터포털(실데이터, T+1)"; else warn "시세: 가상 시장(MOCK) + 가속 시뮬레이션"; fi
filled DART_API_KEY && say "재무·공시: OpenDART" || true
filled NAVER_CLIENT_ID && say "뉴스: 네이버 검색" || true
echo

# 이 프로젝트 이미지 ID (backend/frontend). 다른 프로젝트·베이스 이미지는 건드리지 않는다.
project_images() { docker compose config --images 2>/dev/null | grep -E -- '-(backend|frontend)$' | xargs -r docker image inspect --format '{{.Id}}' 2>/dev/null | sort -u; }

# 재빌드 후, 이번 빌드로 대체된 이 프로젝트의 이전 이미지만 삭제 (용량 정리)
remove_old_images() {
  local before="$1" after old
  after="$(project_images)"
  for old in $before; do
    if ! grep -qx "$old" <<<"$after"; then
      docker image rm "$old" >/dev/null 2>&1 && say "이전 이미지 삭제: ${old:7:12}" || true
    fi
  done
}

docker_ok() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

run_docker() {
  docker_ok || die "Docker 데몬에 연결할 수 없어요. Docker Desktop 을 켜거나 './run.sh local' 로 실행하세요."
  say "docker compose 로 빌드합니다 (처음엔 몇 분 걸려요)"
  local before
  before="$(project_images)"
  docker compose build
  remove_old_images "$before"
  say "준비되면 브라우저에서 ${URL} 을 여세요. 종료: Ctrl+C"
  exec docker compose up
}

run_up() {
  docker_ok || die "Docker 데몬에 연결할 수 없어요. Docker Desktop 을 켜거나 './run.sh local' 로 실행하세요."
  say "docker compose 를 백그라운드로 기동합니다 (처음엔 이미지 빌드로 몇 분 걸려요)"
  local before
  before="$(project_images)"
  docker compose build
  docker compose up -d
  remove_old_images "$before"
  for _ in $(seq 1 90); do
    curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/api/health" >/dev/null 2>&1 && break
    sleep 2
  done
  curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/api/health" >/dev/null 2>&1 \
    || die "기동 확인 실패. 'docker compose logs backend' 로 로그를 확인하세요."
  printf '\n%s\n' "${BOLD}✅ 준비 완료: ${URL}${RESET}"
  printf '%s\n\n' "   API 문서: http://localhost:${BACKEND_PORT}/api/docs · 로그: docker compose logs -f · 중지: ./run.sh down"
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
  for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
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

  say "백엔드 기동 → http://127.0.0.1:${BACKEND_PORT} (API 문서 /api/docs)"
  (cd backend && exec uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port "$BACKEND_PORT") &
  pids+=($!)
  for _ in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null 2>&1 || die "백엔드가 뜨지 않았어요. 위 로그를 확인하세요."

  say "프론트엔드 기동 → ${URL}"
  (cd frontend && VITE_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}" exec npx vite --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort) &
  pids+=($!)
  sleep 2
  printf '\n%s\n\n' "${BOLD}✅ 준비 완료: ${URL}  (처음이면 로컬 계정을 만드세요 · 종료: Ctrl+C)${RESET}"
  wait
}

run_tests() {
  (cd backend && uv sync --frozen --python 3.12 >/dev/null && uv run pytest -q --cov=app --cov-report=term-missing:skip-covered)
  (cd frontend && { [[ -d node_modules ]] || npm ci --no-audit --no-fund >/dev/null; } && npm test)
}

case "$MODE" in
  docker) run_docker ;;
  up) run_up ;;
  down) docker compose down ;;
  local) run_local ;;
  test) run_tests ;;
  auto) if docker_ok; then run_docker; else warn "Docker 가 꺼져 있어 로컬 모드로 실행합니다"; run_local; fi ;;
  *) die "사용법: ./run.sh [auto|docker|up|down|local|test]" ;;
esac
