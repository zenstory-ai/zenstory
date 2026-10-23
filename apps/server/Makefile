# Makefile for running tests in the zenstory backend project
# Usage: make [target]

# Variables
PYTHON = python3
# Prefer Python 3.12 virtualenv to avoid dependency/runtime mismatches (e.g. py3.14).
VENV ?= $(shell if [ -d .venv312 ]; then echo .venv312; elif [ -d .venv ]; then echo .venv; elif [ -d venv ]; then echo venv; else echo venv; fi)
VENV_PYTHON = $(VENV)/bin/python
VENV_PYTEST = $(VENV)/bin/pytest
DOCKER_COMPOSE = docker-compose
TEST_FILE = docker-compose.test.yml

# Colors for output
COLOR_RESET = \033[0m
COLOR_BOLD = \033[1m
COLOR_GREEN = \033[32m
COLOR_YELLOW = \033[33m
COLOR_BLUE = \033[34m

.PHONY: help env-check test test-cov test-unit test-integration test-collect test-fast test-risk test-deep test-wave-a test-wave-b test-wave-c test-wave-d test-env-up test-env-down test-clean test-verbose preflight-release smoke-release

# Default target
.DEFAULT_GOAL := help

## help: Show this help message
help:
	@echo "$(COLOR_BOLD)zenstory Backend Test Commands$(COLOR_RESET)"
	@echo ""
	@echo "$(COLOR_GREEN)Testing:$(COLOR_RESET)"
	@grep -E '^## ' Makefile | sed 's/## /  /' | sed 's/:/ -/' | column -t -s -

## env-check: Show current test Python/pytest environment
env-check:
	@echo "$(COLOR_BLUE)Using venv: $(VENV)$(COLOR_RESET)"
	@$(VENV_PYTHON) -V
	@$(VENV_PYTHON) scripts/check_python_runtime.py
	@$(VENV_PYTHON) -c "import sqlmodel; print('sqlmodel', sqlmodel.__version__)"

## test: Run all tests
test:
	@echo "$(COLOR_BLUE)Running all tests...$(COLOR_RESET)"
	$(VENV_PYTEST)

## test-cov: Run tests with coverage report
test-cov:
	@echo "$(COLOR_BLUE)Running tests with coverage...$(COLOR_RESET)"
	$(VENV_PYTEST) --cov=. --cov-report=html --cov-report=term
	@echo "$(COLOR_GREEN)Coverage report generated in htmlcov/index.html$(COLOR_RESET)"

## test-unit: Run only unit tests
test-unit:
	@echo "$(COLOR_BLUE)Running unit tests...$(COLOR_RESET)"
	$(VENV_PYTEST) -m unit

## test-integration: Run only integration tests
test-integration:
	@echo "$(COLOR_BLUE)Running integration tests...$(COLOR_RESET)"
	$(VENV_PYTEST) -m integration

## test-collect: Collect tests without default coverage/xdist addopts
test-collect:
	@echo "$(COLOR_BLUE)Collecting tests with stripped addopts...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' --collect-only -q tests

## test-fast: Run fast lane (services/core/utils/middleware) without coverage/xdist overhead
test-fast:
	@echo "$(COLOR_BLUE)Running fast lane...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' tests/test_services tests/test_models tests/test_utils tests/test_core tests/test_middleware -q

## test-risk: Run risk lane (auth/oauth/files/subscription/materials) without coverage/xdist overhead
test-risk:
	@echo "$(COLOR_BLUE)Running risk lane...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' \
		tests/test_api/test_auth.py \
		tests/test_api/test_auth_helpers.py \
		tests/test_api/test_oauth.py \
		tests/test_api/test_oauth_helpers.py \
		tests/test_api/test_files.py \
		tests/test_api/test_files_helpers.py \
		tests/test_api/test_subscription.py \
		tests/test_api/test_subscription_helpers.py \
		tests/test_api/test_subscription_plans_catalog.py \
		tests/test_api/test_materials.py \
		tests/test_api/test_materials_helpers.py \
		tests/test_api/test_materials_retry.py -q

## test-deep: Run deep lane (agent/flow-heavy modules) without coverage/xdist overhead
test-deep:
	@echo "$(COLOR_BLUE)Running deep lane...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' tests/test_agent tests/test_flows -q

## test-wave-a: Run Wave A service targets
test-wave-a:
	@echo "$(COLOR_BLUE)Running Wave A targets...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' tests/test_services -q

## test-wave-b: Run Wave B API targets
test-wave-b:
	@echo "$(COLOR_BLUE)Running Wave B targets...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' \
		tests/test_api/test_auth.py \
		tests/test_api/test_auth_helpers.py \
		tests/test_api/test_oauth.py \
		tests/test_api/test_oauth_helpers.py \
		tests/test_api/test_files.py \
		tests/test_api/test_files_helpers.py \
		tests/test_api/test_subscription.py \
		tests/test_api/test_subscription_helpers.py \
		tests/test_api/test_subscription_plans_catalog.py \
		tests/test_api/test_materials_helpers.py -q

## test-wave-c: Run Wave C agent targets
test-wave-c:
	@echo "$(COLOR_BLUE)Running Wave C targets...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' tests/test_agent -q

## test-wave-d: Run Wave D support/long-tail targets
test-wave-d:
	@echo "$(COLOR_BLUE)Running Wave D targets...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' tests/test_core tests/test_utils tests/test_middleware tests/test_api/test_materials.py tests/test_api/test_materials_retry.py tests/test_api/test_admin*.py -q

## test-slow: Run only slow tests
test-slow:
	@echo "$(COLOR_BLUE)Running slow tests...$(COLOR_RESET)"
	$(VENV_PYTEST) -m slow

## test-verbose: Run tests with verbose output
test-verbose:
	@echo "$(COLOR_BLUE)Running tests with verbose output...$(COLOR_RESET)"
	$(VENV_PYTEST) -vv -s

## test-watch: Watch for changes and re-run tests
test-watch:
	@echo "$(COLOR_BLUE)Watching for changes...$(COLOR_RESET)"
	$(VENV)/bin/pytest-watch

## test-env-up: Start test environment (Docker)
test-env-up:
	@echo "$(COLOR_BLUE)Starting test environment...$(COLOR_RESET)"
	$(DOCKER_COMPOSE) -f $(TEST_FILE) up -d
	@echo "$(COLOR_GREEN)Test environment started!$(COLOR_RESET)"
	@echo "PostgreSQL: localhost:5433"
	@echo "Redis: localhost:6380"

## test-env-down: Stop test environment (Docker)
test-env-down:
	@echo "$(COLOR_BLUE)Stopping test environment...$(COLOR_RESET)"
	$(DOCKER_COMPOSE) -f $(TEST_FILE) down
	@echo "$(COLOR_GREEN)Test environment stopped!$(COLOR_RESET)"

## test-env-logs: Show test environment logs
test-env-logs:
	$(DOCKER_COMPOSE) -f $(TEST_FILE) logs -f

## test-clean: Clean test artifacts
test-clean:
	@echo "$(COLOR_BLUE)Cleaning test artifacts...$(COLOR_RESET)"
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf test.db
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "$(COLOR_GREEN)Cleaned test artifacts!$(COLOR_RESET)"

## test-db-reset: Reset test database
test-db-reset:
	@echo "$(COLOR_BLUE)Resetting test database...$(COLOR_RESET)"
	$(DOCKER_COMPOSE) -f $(TEST_FILE) down -v
	$(DOCKER_COMPOSE) -f $(TEST_FILE) up -d
	@echo "$(COLOR_GREEN)Test database reset!$(COLOR_RESET)"

## preflight-release: Run release preflight checks (S3 backends write, read back and delete one synthetic object)
preflight-release:
	@echo "$(COLOR_BLUE)Running release preflight checks...$(COLOR_RESET)"
	$(VENV_PYTHON) scripts/release_preflight_check.py --strict --probe-upload-storage
	@echo "$(COLOR_GREEN)Release preflight checks passed!$(COLOR_RESET)"

## smoke-release: Run critical backend smoke tests before release
smoke-release:
	@echo "$(COLOR_BLUE)Running release smoke test suite...$(COLOR_RESET)"
	$(VENV_PYTEST) -o addopts='' \
		tests/test_services/test_data_isolation.py \
		tests/test_concurrent_isolation.py \
		tests/test_api/test_agent.py \
		tests/test_api/test_chat.py \
		tests/test_api/test_agent_api.py \
		tests/test_services/test_agent_auth_service.py \
		-q
	@echo "$(COLOR_GREEN)Release smoke suite passed!$(COLOR_RESET)"
