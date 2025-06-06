format:
    uv run ruff format .

test: format
    PYTHONPATH=. uv run pytest -v tests/

clean:
    rm -rf dist/*

build: clean
    uv build

publish: test build
    uv publish
