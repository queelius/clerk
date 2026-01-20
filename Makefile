.PHONY: test test-unit test-integration lint typecheck clean build

# Run all unit tests
test: test-unit

test-unit:
	pytest tests/ -v --ignore=tests/integration

# Run integration tests (requires Docker)
test-integration:
	docker-compose -f docker-compose.test.yml up -d
	sleep 3
	pytest tests/integration/ -v || (docker-compose -f docker-compose.test.yml down && exit 1)
	docker-compose -f docker-compose.test.yml down

# Run all tests including integration
test-all: test-unit test-integration

# Coverage report
coverage:
	pytest tests/ --ignore=tests/integration --cov=src/clerk --cov-report=html --cov-report=term

# Lint
lint:
	ruff check src tests

# Format
format:
	ruff format src tests

# Type check
typecheck:
	mypy src/clerk

# Clean build artifacts
clean:
	rm -rf build/ dist/ *.egg-info
	rm -rf .pytest_cache .coverage htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Build package
build: clean
	python -m build

# Install in development mode
dev:
	pip install -e ".[dev]"
