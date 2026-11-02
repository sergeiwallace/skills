# Local verification gate. `make check` runs the test suite and ruff over the Python
# that ships a test suite here. Every dependency resolves through uv; nothing is
# installed into the system interpreter.
UV_TEST := uv run --no-project --with pytest --with "typer>=0.12" \
	--with "pydantic-settings>=2.2" \
	--with-editable packages/skill-cli-foundation

.PHONY: check test lint

check: test lint

test:
	$(UV_TEST) pytest -q

lint:
	uvx ruff check packages/skill-cli-foundation
