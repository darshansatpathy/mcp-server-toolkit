.PHONY: install check test contract dev-keys serve token bench image

install:
	pip install -e ".[dev]"

check:
	ruff check .
	ruff format --check .
	mypy
	mcpkit contract --check
	pytest --cov --cov-report=term-missing

contract:
	mcpkit contract

dev-keys:
	mcpkit dev-keys

serve:
	mcpkit serve

token:
	@mcpkit dev-token

bench:
	python bench/load.py --clients 32 --calls 100 --out docs/performance.md

image:
	docker build -t mcp-server-toolkit:dev .
