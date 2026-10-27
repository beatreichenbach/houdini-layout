# Contributing

## Development

To get started:

```sh
uv venv --python 3.11
uv pip install -e ".[dev]"
uv run pre-commit install
```

Run the checks:

```sh
uv run ruff format node
uv run ruff check --select I --fix node
uv run ruff check node
uv run ty check
```

## Project Structure

| Directory | Description                                                                                                         |
|-----------|---------------------------------------------------------------------------------------------------------------------|
| `src/`    | Digital Assets and the package definition. During the release action they get packaged in the `layout.zip` archive. |
| `node/`   | The source code for the ViewerState and the HDA Module which is stored in the HDA.                                  |
