#!/usr/bin/env bash
# AI 펀드매니저 실행 스크립트 (기본 모의투자, TRADING_MODE=live 면 토스증권 실거래)
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
  warn ".env 가 없어서 .env.example 로 만들었어요."
  warn ".env 에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET(필수)과 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 를 채운 뒤 다시 실행하세요."
fi

# 거래 모드: paper(기본, 모의투자) | live(토스증권 실계좌 실제 주문 — 확인 문구 필요)
tm="$(grep -E '^[[:space:]]*TRADING_MODE[[:space:]]*=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'" | tr '[:upper:]' '[:lower:]' || true)"
tm="${tm:-paper}"
case "$tm" in
  paper) ;;
  live)
    confirm="$(grep -E '^[[:space:]]*LIVE_TRADING_CONFIRM[[:space:]]*=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'" || true)"
    [[ "$confirm" == "I_UNDERSTAND_REAL_MONEY" ]] \
      || die "TRADING_MODE=live 는 실제 돈으로 주문합니다. 켜려면 .env 에 LIVE_TRADING_CONFIRM=I_UNDERSTAND_REAL_MONEY 를 함께 넣으세요."
    printf '\n%s\n' "${RED}${BOLD}🔴 실거래(LIVE) 모드 — 토스증권 실계좌로 실제 주문이 나갑니다.${RESET}"
    printf '%s\n' "${RED}   하루 매수 상한 LIVE_DAILY_BUY_LIMIT, 1회 주문 한도(설정 화면), 킬 스위치를 확인하세요.${RESET}"
    ;;
  *) die "TRADING_MODE=$tm — 'paper'(모의투자) 또는 'live'(실거래)만 쓸 수 있어요." ;;
esac

# 휴대폰 알림(Web Push) 서명키: 없으면 만들어 .env 에 넣는다 (값은 화면에 출력하지 않음)
if ! grep -qE '^[[:space:]]*VAPID_PRIVATE_KEY[[:space:]]*=[[:space:]]*[^[:space:]#]+' .env; then
  # P-256 개인키 = 곡선 위수(n)보다 작은 32바이트 난수 (base64url)
  if vapid="$(python3 -c 'import base64,secrets
n=0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
while True:
    k=secrets.token_bytes(32)
    if 0<int.from_bytes(k,"big")<n: break
print(base64.urlsafe_b64encode(k).rstrip(b"=").decode())' 2>/dev/null)" && [[ -n "$vapid" ]]; then
    { grep -vE '^[[:space:]]*VAPID_PRIVATE_KEY[[:space:]]*=' .env || true; printf 'VAPID_PRIVATE_KEY=%s\n' "$vapid"; } > .env.tmp
    cat .env.tmp > .env && rm -f .env.tmp
    unset vapid
    say "휴대폰 알림 서명키(VAPID)를 만들어 .env 에 넣었어요"
  else
    warn "python3 가 없어 휴대폰 알림 서명키를 만들지 못했어요 (알림만 꺼지고 나머지는 정상)"
  fi
fi

# 업데이트(git pull) 후 .env.example 에 새로 생긴 설정 안내 (키 이름만 출력, 값은 건드리지 않음)
missing=()
while IFS= read -r key; do
  grep -qE "^[[:space:]]*#?[[:space:]]*${key}[[:space:]]*=" .env || missing+=("$key")
done < <(grep -oE '^[A-Z][A-Z0-9_]*=' .env.example | tr -d '=')
if (( ${#missing[@]} )); then
  warn ".env.example 에 새 설정이 있어요 (없으면 기본값 사용): ${missing[*]}"
fi

env_val() { grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'" || true; }
FRONTEND_PORT="${FRONTEND_PORT:-$(env_val FRONTEND_PORT)}"; FRONTEND_PORT="${FRONTEND_PORT:-15173}"
BACKEND_PORT="${BACKEND_PORT:-$(env_val BACKEND_PORT)}"; BACKEND_PORT="${BACKEND_PORT:-18000}"
export FRONTEND_PORT BACKEND_PORT
URL="http://localhost:${FRONTEND_PORT}"

filled() { grep -qE "^[[:space:]]*$1[[:space:]]*=[[:space:]]*[^[:space:]#]+" .env; }
echo
if [[ "$tm" == "live" ]]; then printf '%s\n' "${BOLD}🦉 AI 펀드매니저 — ${RED}실거래(LIVE)${RESET}"
else printf '%s\n' "${BOLD}🦉 AI 펀드매니저 — 모의투자(PAPER)${RESET}"; fi
if filled LLM_BASE_URL && filled LLM_MODEL && filled LLM_API_KEY; then say "LLM: 설정됨"; else warn "LLM: 미설정 → 규칙 기반 매니저"; fi
if filled TOSS_CLIENT_ID && filled TOSS_CLIENT_SECRET; then say "시세: 토스증권 Open API (실제 종목·실시간, 조회 전용)"
else die ".env 에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 필요합니다 (토스증권 WTS > 설정 > Open API, 허용 IP 등록 필수)"; fi
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
  # 처음이거나 git pull 로 package-lock.json 이 바뀌었으면 다시 설치
  if [[ ! -f frontend/node_modules/.package-lock.json || frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]]; then
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
  (cd frontend && { [[ -f node_modules/.package-lock.json && ! package-lock.json -nt node_modules/.package-lock.json ]] || npm ci --no-audit --no-fund >/dev/null; } && npm test)
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
