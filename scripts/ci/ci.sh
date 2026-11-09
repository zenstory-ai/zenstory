#!/bin/bash
# ci.sh - Unified CI runner
# Usage: ./scripts/ci/ci.sh [command] [--docker|--lite]
#
# Commands:
#   all       Run full CI pipeline (lint + test + e2e)
#   frontend  Run frontend lint and tests only
#   backend   Run backend lint and tests only
#   lint      Run lint checks only
#   test      Run unit tests only (no e2e)
#   e2e       Run E2E tests only (--docker or --lite)
#   start     Start Docker services (--docker mode only)
#   stop      Stop Docker services (--docker mode only)
#   help      Show this help message
#
# Modes:
#   --docker  Use Docker environment (PostgreSQL + Redis) - default
#   --lite    Use SQLite lightweight mode (no Docker required)
#
# Examples:
#   ./scripts/ci/ci.sh all                    # Full CI with Docker
#   ./scripts/ci/ci.sh all --lite             # Full CI with SQLite
#   ./scripts/ci/ci.sh backend --lite         # Backend tests with SQLite
#   E2E_SUITE=default ./scripts/ci/ci.sh e2e                    # Default required browser lane
#   E2E_SUITE=nightly ./scripts/ci/ci.sh e2e --lite             # Nightly browser lane (SQLite lite mode)
#   E2E_SUITE=release ./scripts/ci/ci.sh e2e --lite             # Release/manual browser lane
#   ./scripts/ci/ci.sh start                  # Start Docker services
#   ./scripts/ci/ci.sh stop                   # Stop Docker services

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER_DIR="$PROJECT_ROOT/apps/server"
WEB_DIR="$PROJECT_ROOT/apps/web"
DOCKER_COMPOSE_FILE="$SERVER_DIR/docker-compose.test.yml"

# Parse arguments
COMMAND="${1:-help}"
MODE="${2:---docker}"

# Validate mode
if [ "$MODE" != "--docker" ] && [ "$MODE" != "--lite" ]; then
    echo -e "${RED}Error: Invalid mode '$MODE'. Use --docker or --lite${NC}"
    exit 1
fi

# Docker mode environment variables
DOCKER_DATABASE_URL="postgresql://test:test@localhost:5433/zenstory_test"
DOCKER_REDIS_URL="redis://localhost:6380/1"

# Lite mode environment variables
LITE_DATABASE_URL="sqlite:///./test_zenstory.db"
LITE_REDIS_URL="redis://localhost:6379/1"

# Common test environment variables
export OPENAI_API_KEY="test-key-for-ci-testing"
export JWT_SECRET_KEY="test-secret-key-min-32-characters-for-ci-testing"
export DEEPSEEK_API_KEY="test-key-for-ci-testing"
export E2E_TEST_EMAIL="e2e-test@example.com"
export E2E_TEST_PASSWORD="E2eTestPassword123!"
export E2E_TEST_USERNAME="e2e_test_user"
export E2E_TEST_INVITE_CODE="E2E1-TST1"

# Flags for Docker mode
CLEANUP_ON_EXIT=true
SERVICES_STARTED=false

# ============================================================================
# Common Functions
# ============================================================================

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

show_usage() {
    cat << EOF
Usage: $0 <command> [--docker|--lite]

Commands:
    all       Run full CI pipeline (lint + test + e2e)
    frontend  Run frontend lint and tests only
    backend   Run backend lint and tests only
    lint      Run lint checks only
    test      Run unit tests only (no e2e)
    e2e       Run E2E tests only (--docker or --lite)
    start     Start Docker services (--docker mode only)
    stop      Stop Docker services (--docker mode only)
    help      Show this help message

Modes:
    --docker  Use Docker environment (PostgreSQL + Redis) - default
    --lite    Use SQLite lightweight mode (no Docker required)

Examples:
    $0 all                    # Full CI with Docker
    $0 all --lite             # Full CI with SQLite
    $0 backend --lite         # Backend tests with SQLite
    E2E_SUITE=default $0 e2e                    # Default required browser lane (Docker)
    E2E_SUITE=nightly $0 e2e --lite             # Nightly browser lane (SQLite lite mode)
    E2E_SUITE=release $0 e2e --lite             # Release/manual browser lane (SQLite lite mode)
    E2E_SUITE=full $0 e2e                       # Full browser lane (all enabled suites)
    $0 start                  # Start Docker services
    $0 stop                   # Stop Docker services

EOF
    exit 0
}

# ============================================================================
# Docker Mode Functions (from ci-local.sh)
# ============================================================================

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "docker-compose is not installed. Please install docker-compose first."
        exit 1
    fi
}

cleanup() {
    if [ "$CLEANUP_ON_EXIT" = true ] && [ "$SERVICES_STARTED" = true ]; then
        log_info "Cleaning up Docker services..."
        cd "$SERVER_DIR"
        docker-compose -f docker-compose.test.yml down --volumes --remove-orphans 2>/dev/null || true
        log_info "Cleanup complete"
    fi
}

start_docker_services() {
    log_step "Starting Docker services..."

    cd "$SERVER_DIR"

    # Start services using docker-compose
    if docker-compose -f "$DOCKER_COMPOSE_FILE" up -d 2>/dev/null; then
        : # Use docker-compose
    elif docker compose -f "$DOCKER_COMPOSE_FILE" up -d 2>/dev/null; then
        : # Use docker compose (v2)
    else
        log_error "Failed to start Docker services"
        return 1
    fi

    SERVICES_STARTED=true

    # Wait for services to be healthy
    log_step "Waiting for services to be ready..."
    if bash "$PROJECT_ROOT/scripts/wait-for-services.sh"; then
        log_info "All services are ready!"
        return 0
    else
        log_error "Services failed to start"
        return 1
    fi
}

stop_docker_services() {
    log_step "Stopping Docker services..."

    cd "$SERVER_DIR"

    # Disable cleanup on exit since we're doing it manually
    CLEANUP_ON_EXIT=false

    if docker-compose -f "$DOCKER_COMPOSE_FILE" down --volumes --remove-orphans 2>/dev/null; then
        : # Use docker-compose
    elif docker compose -f "$DOCKER_COMPOSE_FILE" down --volumes --remove-orphans 2>/dev/null; then
        : # Use docker compose (v2)
    else
        log_warn "Some cleanup may have failed, but continuing..."
    fi

    log_info "Services stopped and cleaned up"
}

run_docker_migrations() {
    log_step "Running database migrations..."

    cd "$SERVER_DIR"
    activate_venv

    # Create test environment file
    cat > .env.test << EOF
DATABASE_URL=$DOCKER_DATABASE_URL
REDIS_URL=$DOCKER_REDIS_URL
OPENAI_API_KEY=$OPENAI_API_KEY
JWT_SECRET_KEY=$JWT_SECRET_KEY
EOF

    # Ensure app/alembic load Docker test database config
    cp .env.test .env
    export DATABASE_URL="$DOCKER_DATABASE_URL"
    export REDIS_URL="$DOCKER_REDIS_URL"
    export OPENAI_API_KEY="$OPENAI_API_KEY"
    export JWT_SECRET_KEY="$JWT_SECRET_KEY"

    # Align with GitHub CI bootstrapping for ephemeral test DBs:
    # 1) create all tables from SQLModel metadata
    # 2) mark schema as migrated at current head
    if python -c "import models; from database import init_db; import asyncio; asyncio.run(init_db())" \
        && alembic stamp head; then
        log_info "Migrations completed successfully"
        return 0
    else
        log_error "Migrations failed"
        return 1
    fi
}

# ============================================================================
# Lite Mode Functions (from local-ci.sh)
# ============================================================================

activate_venv() {
    if [ -d "$SERVER_DIR/.venv312" ]; then
        # shellcheck disable=SC1091
        source "$SERVER_DIR/.venv312/bin/activate"
    elif [ -d "$SERVER_DIR/.venv" ]; then
        # shellcheck disable=SC1091
        source "$SERVER_DIR/.venv/bin/activate"
    elif [ -d "$SERVER_DIR/venv" ]; then
        # shellcheck disable=SC1091
        source "$SERVER_DIR/venv/bin/activate"
    fi

    python "$SERVER_DIR/scripts/check_python_runtime.py"
}

cleanup_lite_db() {
    rm -f "$SERVER_DIR/test_zenstory.db"
}

# ============================================================================
# Test Functions
# ============================================================================

run_playwright_suite() {
    local suite="${E2E_SUITE:-smoke}"
    local -a args
    # Backend is started by this CI script; let Playwright only manage the frontend dev server.
    local -a env_args=(
        CI=true
        PLAYWRIGHT_EXTERNAL_BACKEND=1
        NO_PROXY=127.0.0.1,localhost
        no_proxy=127.0.0.1,localhost
    )

    case "$suite" in
        smoke)
            log_info "Running smoke browser lane (E2E_SUITE=smoke)"
            args=(
                e2e/smoke.spec.ts
                --project=chromium
            )
            ;;
        default)
            log_info "Running default required browser lane (E2E_SUITE=default)"
            args=(
                e2e/auth.spec.ts
                e2e/session.spec.ts
                e2e/projects.spec.ts
                e2e/points.spec.ts
                e2e/onboarding-persona.spec.ts
                e2e/public-skills.spec.ts
                e2e/referral.spec.ts
                e2e/security.spec.ts
                e2e/subscription.spec.ts
                e2e/settings-regression.spec.ts
                e2e/settings.spec.ts
                e2e/smoke.spec.ts
                --project=chromium
            )
            ;;
        nightly)
            log_info "Running nightly browser lane (E2E_SUITE=nightly)"
            args=(
                e2e/versions.spec.ts
                e2e/skills.spec.ts
                e2e/skills-flow.spec.ts
                --project=chromium
            )
            env_args+=(
                E2E_ENABLE_VERSION_HISTORY_E2E=true
                E2E_ENABLE_SKILL_CREATE_E2E=true
            )
            ;;
        release)
            log_info "Running release/manual browser lane (E2E_SUITE=release)"
            args=(
                e2e/visual.spec.ts
                e2e/performance.spec.ts
                e2e/large-document.spec.ts
                e2e/concurrent.spec.ts
                e2e/voice.spec.ts
                e2e/voice-input-mocked.spec.ts
                --project=chromium
            )
            env_args+=(
                E2E_ENABLE_VISUAL_REGRESSION_E2E=true
                E2E_ENABLE_PERFORMANCE_E2E=true
                E2E_ENABLE_LARGE_DOCUMENT_E2E=true
                E2E_ENABLE_CONCURRENT_E2E=true
                E2E_ENABLE_VOICE_INPUT_E2E=true
            )
            ;;
        full)
            log_info "Running full browser lane (E2E_SUITE=full)"
            args=(--project=chromium)
            env_args+=(
                E2E_ENABLE_CHAT_E2E=true
                E2E_ENABLE_CONCURRENT_E2E=true
                E2E_ENABLE_DEEP_LINK_E2E=true
                E2E_ENABLE_ERROR_RECOVERY_E2E=true
                E2E_ENABLE_EXPORT_E2E=true
                E2E_ENABLE_FILES_E2E=true
                E2E_ENABLE_FILE_SEARCH_E2E=true
                E2E_ENABLE_MOCKED_CHAT=true
                E2E_ENABLE_VERSION_HISTORY_E2E=true
                E2E_ENABLE_SKILL_CREATE_E2E=true
                E2E_ENABLE_VISUAL_REGRESSION_E2E=true
                E2E_ENABLE_PERFORMANCE_E2E=true
                E2E_ENABLE_LARGE_DOCUMENT_E2E=true
                E2E_ENABLE_VOICE_INPUT_E2E=true
            )
            ;;
        *)
            log_error "Unknown E2E_SUITE '$suite'. Supported: smoke, default, nightly, release, full"
            return 1
            ;;
    esac

    env "${env_args[@]}" pnpm exec playwright test "${args[@]}"
}

run_frontend_lint() {
    log_step "Running frontend lint checks..."

    cd "$WEB_DIR"
    if pnpm lint; then
        log_info "Frontend lint passed!"
        return 0
    else
        log_error "Frontend lint failed"
        return 1
    fi
}

run_backend_lint() {
    log_step "Running backend lint checks..."

    cd "$SERVER_DIR"
    activate_venv

    if ruff check agent/ api/ services/; then
        log_info "Backend lint passed!"
        return 0
    else
        log_error "Backend lint failed"
        return 1
    fi
}

run_lint() {
    local failed=0

    run_frontend_lint || failed=1
    run_backend_lint || failed=1

    if [ $failed -eq 0 ]; then
        log_info "All lint checks passed!"
        return 0
    else
        log_error "Some lint checks failed"
        return 1
    fi
}

run_frontend_tests() {
    log_step "Running frontend tests..."

    cd "$WEB_DIR"
    if pnpm test:coverage; then
        log_info "Frontend tests passed!"
        return 0
    else
        log_error "Frontend tests failed"
        return 1
    fi
}

run_cli_tests() {
    log_step "Running CLI (apps/cli) tests and build..."

    cd "$PROJECT_ROOT"
    if pnpm --filter zenstory test && pnpm --filter zenstory build; then
        log_info "CLI tests passed!"
        return 0
    else
        log_error "CLI tests failed"
        return 1
    fi
}

run_backend_tests_docker() {
    log_step "Running backend tests (Docker mode)..."

    cd "$SERVER_DIR"
    activate_venv

    # Create test environment file
    cat > .env.test << EOF
DATABASE_URL=$DOCKER_DATABASE_URL
REDIS_URL=$DOCKER_REDIS_URL
OPENAI_API_KEY=$OPENAI_API_KEY
JWT_SECRET_KEY=$JWT_SECRET_KEY
EOF

    # Run pytest
    if pytest --cov=. --cov-report=xml --cov-report=term -v; then
        log_info "Backend tests passed!"
        return 0
    else
        log_error "Backend tests failed"
        return 1
    fi
}

run_backend_tests_lite() {
    log_step "Running backend tests (Lite mode)..."

    cd "$SERVER_DIR"

    # Set test environment variables
    export DATABASE_URL="$LITE_DATABASE_URL"
    export REDIS_URL="$LITE_REDIS_URL"

    activate_venv

    # Run tests
    if pytest --cov=. --cov-report=term -v; then
        cleanup_lite_db
        log_info "Backend tests passed!"
        return 0
    else
        cleanup_lite_db
        log_error "Backend tests failed"
        return 1
    fi
}

run_backend_tests() {
    if [ "$MODE" = "--lite" ]; then
        run_backend_tests_lite
    else
        run_backend_tests_docker
    fi
}

run_e2e_tests_docker() {
    log_step "Running E2E tests (Docker mode)..."

    # Explicitly exercise the optional library in full browser regression.
    export INSPIRATIONS_ENABLED="${INSPIRATIONS_ENABLED:-true}"
    export VITE_INSPIRATIONS_ENABLED="${VITE_INSPIRATIONS_ENABLED:-$INSPIRATIONS_ENABLED}"

    cd "$SERVER_DIR"
    activate_venv

    # Create test environment file
    cat > .env.test << EOF
DATABASE_URL=$DOCKER_DATABASE_URL
REDIS_URL=$DOCKER_REDIS_URL
OPENAI_API_KEY=$OPENAI_API_KEY
JWT_SECRET_KEY=$JWT_SECRET_KEY
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY
E2E_TEST_EMAIL=$E2E_TEST_EMAIL
E2E_TEST_PASSWORD=$E2E_TEST_PASSWORD
E2E_TEST_USERNAME=$E2E_TEST_USERNAME
EOF

    # Run migrations
    cp .env.test .env
    if ! run_docker_migrations; then
        log_error "Failed to run migrations for E2E tests"
        return 1
    fi

    # Seed test user
    log_info "Seeding test user..."
    if [ -f scripts/seed_test_user.py ]; then
        python scripts/seed_test_user.py
    else
        log_warn "seed_test_user.py not found, skipping..."
    fi

    # Start backend server in background
    log_info "Starting backend server..."
    PYTHONUNBUFFERED=1 AUTH_RATE_LIMIT_ENABLED=false python main.py > /tmp/zenstory_backend.log 2>&1 &
    local backend_pid=$!

    # Wait for backend to be healthy
    log_info "Waiting for backend to be ready..."
    local count=0
    while [ $count -lt 30 ]; do
        if curl -f http://localhost:8000/health >/dev/null 2>&1; then
            log_info "Backend is healthy!"
            break
        fi
        count=$((count + 1))
        sleep 1
    done

    if [ $count -ge 30 ]; then
        log_error "Backend failed to start"
        kill $backend_pid 2>/dev/null || true
        return 1
    fi

    # Run Playwright tests
    cd "$WEB_DIR"
    local test_result=0

    if ! run_playwright_suite; then
        log_error "E2E tests failed"
        test_result=1
    fi

    # Cleanup: kill backend server
    log_info "Stopping backend server..."
    kill $backend_pid 2>/dev/null || true

    if [ $test_result -eq 0 ]; then
        log_info "E2E tests passed!"
        return 0
    else
        log_error "E2E tests failed"
        return 1
    fi
}

run_e2e_tests_lite() {
    # These servers start before Playwright reads its managed-server settings.
    export INSPIRATIONS_ENABLED="${INSPIRATIONS_ENABLED:-true}"
    export VITE_INSPIRATIONS_ENABLED="${VITE_INSPIRATIONS_ENABLED:-$INSPIRATIONS_ENABLED}"
    log_step "Running E2E tests (Lite mode)..."

    cd "$SERVER_DIR"
    export DATABASE_URL="$LITE_DATABASE_URL"
    export REDIS_URL="$LITE_REDIS_URL"
    activate_venv

    # Ensure schema exists for standalone Lite E2E runs.
    log_info "Initializing Lite test database..."
    if ! python -c "import models; from database import init_db; import asyncio; asyncio.run(init_db())"; then
        log_error "Failed to initialize Lite test database"
        return 1
    fi

    # Seed regular/admin E2E users expected by Playwright auth setup.
    log_info "Seeding test user..."
    if [ -f scripts/seed_test_user.py ]; then
        if ! python scripts/seed_test_user.py; then
            log_error "Failed to seed E2E test users"
            return 1
        fi
    else
        log_warn "seed_test_user.py not found, skipping..."
    fi

    # Start fresh backend server
    lsof -i :8000 -sTCP:LISTEN -t | xargs -r kill 2>/dev/null || true
    log_info "Starting backend server..."
    local backend_pid=""
    PYTHONUNBUFFERED=1 AUTH_RATE_LIMIT_ENABLED=false python main.py > /tmp/zenstory_backend_lite.log 2>&1 &
    backend_pid=$!
    cd "$PROJECT_ROOT"

    # Wait for backend to start
    local count=0
    while [ $count -lt 30 ]; do
        if curl -s http://localhost:8000/health > /dev/null; then
            log_info "Backend is healthy!"
            break
        fi
        count=$((count + 1))
        sleep 1
    done

    if [ $count -ge 30 ]; then
        log_error "Backend failed to start (see /tmp/zenstory_backend_lite.log)"
        tail -n 80 /tmp/zenstory_backend_lite.log || true
        kill $backend_pid 2>/dev/null || true
        return 1
    fi

    # Start fresh frontend dev server
    lsof -i :5173 -sTCP:LISTEN -t | xargs -r kill 2>/dev/null || true
    log_info "Starting frontend dev server..."
    local frontend_pid=""
    cd "$WEB_DIR"
    pnpm dev --host 127.0.0.1 --port 5173 > /tmp/zenstory_frontend_lite.log 2>&1 &
    frontend_pid=$!
    cd "$PROJECT_ROOT"

    local fe_count=0
    while [ $fe_count -lt 60 ]; do
        if curl -s http://127.0.0.1:5173 > /dev/null; then
            log_info "Frontend is ready!"
            break
        fi
        fe_count=$((fe_count + 1))
        sleep 1
    done

    if [ $fe_count -ge 60 ]; then
        log_error "Frontend failed to start (see /tmp/zenstory_frontend_lite.log)"
        tail -n 80 /tmp/zenstory_frontend_lite.log || true
        kill $frontend_pid 2>/dev/null || true
        kill $backend_pid 2>/dev/null || true
        return 1
    fi

    # Run E2E tests
    cd "$WEB_DIR"
    local test_result=0
    if run_playwright_suite; then
        log_info "E2E tests passed!"
    else
        log_error "E2E tests failed"
        test_result=1
    fi
    cd "$PROJECT_ROOT"

    # Cleanup backend/frontend processes
    kill $backend_pid 2>/dev/null || true
    kill $frontend_pid 2>/dev/null || true

    return $test_result
}

run_e2e_tests() {
    if [ "$MODE" = "--lite" ]; then
        run_e2e_tests_lite
    else
        run_e2e_tests_docker
    fi
}

# ============================================================================
# Main Command Handlers
# ============================================================================

run_all_docker() {
    log_step "Running full CI pipeline (Docker mode)..."

    local failed=0

    # Start services if not already running
    if ! docker ps | grep -q "zenstory_postgres_test\|zenstory_redis_test"; then
        if ! start_docker_services; then
            log_error "Failed to start services"
            return 1
        fi
    else
        log_info "Docker services already running"
        SERVICES_STARTED=true
    fi

    # Run lint
    if ! run_lint; then
        log_warn "Lint checks failed, but continuing..."
        failed=1
    fi

    # Run backend tests
    if ! run_backend_tests; then
        log_error "Backend tests failed"
        failed=1
    fi

    # Run frontend tests
    if ! run_frontend_tests; then
        log_error "Frontend tests failed"
        failed=1
    fi

    # Run CLI tests
    if ! run_cli_tests; then
        log_error "CLI tests failed"
        failed=1
    fi

    # Run E2E tests
    if ! run_e2e_tests; then
        log_error "E2E tests failed"
        failed=1
    fi

    if [ $failed -eq 0 ]; then
        log_info "All CI tests passed!"
        return 0
    else
        log_error "Some CI tests failed"
        return 1
    fi
}

run_all_lite() {
    log_step "Running full CI pipeline (Lite mode)..."

    local failed=0

    # Run lint
    if ! run_lint; then
        log_warn "Lint checks failed, but continuing..."
        failed=1
    fi

    # Run backend tests
    if ! run_backend_tests; then
        log_error "Backend tests failed"
        failed=1
    fi

    # Run frontend tests
    if ! run_frontend_tests; then
        log_error "Frontend tests failed"
        failed=1
    fi

    # Run CLI tests
    if ! run_cli_tests; then
        log_error "CLI tests failed"
        failed=1
    fi

    # Skip E2E in lite mode by default
    log_warn "E2E tests skipped in --lite mode. Run separately with: $0 e2e --lite"

    if [ $failed -eq 0 ]; then
        log_info "All CI tests passed!"
        return 0
    else
        log_error "Some CI tests failed"
        return 1
    fi
}

run_all() {
    if [ "$MODE" = "--lite" ]; then
        run_all_lite
    else
        run_all_docker
    fi
}

run_frontend() {
    log_step "Running frontend checks..."

    local failed=0

    run_frontend_lint || failed=1
    run_frontend_tests || failed=1

    return $failed
}

run_backend() {
    log_step "Running backend checks..."

    local failed=0

    if [ "$MODE" = "--docker" ]; then
        check_docker
        if ! docker ps | grep -q "zenstory_postgres_test\|zenstory_redis_test"; then
            start_docker_services || exit 1
        else
            SERVICES_STARTED=true
        fi
    fi

    run_backend_lint || failed=1
    run_backend_tests || failed=1

    return $failed
}

run_test() {
    log_step "Running unit tests..."

    local failed=0

    if [ "$MODE" = "--docker" ]; then
        check_docker
        if ! docker ps | grep -q "zenstory_postgres_test\|zenstory_redis_test"; then
            start_docker_services || exit 1
        else
            SERVICES_STARTED=true
        fi
    fi

    run_backend_tests || failed=1
    run_frontend_tests || failed=1
    run_cli_tests || failed=1

    return $failed
}

# ============================================================================
# Main
# ============================================================================

# Set up cleanup trap for Docker mode
if [ "$MODE" = "--docker" ]; then
    trap cleanup EXIT INT TERM
fi

case "$COMMAND" in
    all)
        run_all
        ;;
    frontend)
        run_frontend
        ;;
    backend)
        run_backend
        ;;
    lint)
        run_lint
        ;;
    test)
        run_test
        ;;
    e2e)
        if [ "$MODE" = "--docker" ]; then
            check_docker
            if ! docker ps | grep -q "zenstory_postgres_test\|zenstory_redis_test"; then
                start_docker_services || exit 1
            else
                SERVICES_STARTED=true
            fi
        fi
        run_e2e_tests
        ;;
    start)
        if [ "$MODE" = "--lite" ]; then
            log_error "Start command is only available in --docker mode"
            exit 1
        fi
        check_docker
        start_docker_services
        log_info "Services started. Press Ctrl+C to stop, or run '$0 stop --docker' to cleanup."
        while true; do
            sleep 3600
        done
        ;;
    stop)
        if [ "$MODE" = "--lite" ]; then
            log_error "Stop command is only available in --docker mode"
            exit 1
        fi
        CLEANUP_ON_EXIT=false
        stop_docker_services
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        log_error "Unknown command: $COMMAND"
        show_usage
        ;;
esac
