PYTHON_VERSION ?= 3.10
UV_PYTHON := $(PYTHON_VERSION)
export UV_PYTHON

.PHONY: sync test lint format check build clean

sync:
	uv sync --group dev --extra magic

test: sync
	uv run pytest

lint: sync
	uv run ruff check herethere tests
	uv run ruff format --check herethere tests
	uv run pylint herethere

format: sync
	uv run ruff format herethere tests

check: lint test

build: sync
	rm -rf dist
	uv run python -m build
	uv run twine check dist/*

venv:
	uv venv --python $(PYTHON_VERSION) --managed-python --seed --clear
	uv lock

clean:
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml dist build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
