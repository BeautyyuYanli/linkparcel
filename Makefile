.PHONY: check format lint serve test typecheck

check: lint typecheck test

format:
	uv run ruff check --fix .
	uv run ruff format .

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run pyright

test:
	uv run pytest -q

serve:
	uv run fastapi dev src/oh_my_pastebin/app.py --port 59000
