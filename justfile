test:
    PYTHONPATH=. uv run pytest tests/

build:
    uv build

publish: test build
    uv publish
