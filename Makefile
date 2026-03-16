.PHONY: test lint typecheck integration smoke all clean fix qa snapshot-update

SCRIPTS := scripts
TESTS := tests

# Default: fast unit tests only
test:
	pytest $(TESTS) -v

# Lint + format check (no auto-fix)
lint:
	ruff check $(SCRIPTS) $(TESTS)
	ruff format --check $(SCRIPTS) $(TESTS)

# Fix lint issues
fix:
	ruff check --fix $(SCRIPTS) $(TESTS)
	ruff format $(SCRIPTS) $(TESTS)

# Type check
typecheck:
	mypy $(SCRIPTS)

# Integration tests (real APIs — use with care)
integration:
	pytest $(TESTS) -m integration -v

# Smoke test (read-only API validation)
smoke:
	pytest $(TESTS) -m smoke -v

# Update syrupy snapshots (run after confirming API output is correct)
snapshot-update:
	pytest $(TESTS) -m integration --snapshot-update -v

# Full QA gate: lint + types + unit tests
qa: lint typecheck test

# Everything including integration
all: qa integration
