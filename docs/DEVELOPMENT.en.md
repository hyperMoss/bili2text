# Development Guide

## Goal

This repository is moving from a legacy script-style layout to a more maintainable application structure:

- `CLI` as the main entrypoint
- `Window / Web / Server` as features
- transcription providers decoupled from the core pipeline
- `uv` as the standard development workflow

## Layout

```text
src/b2t/
  cli.py            CLI entrypoint
  bootstrap.py      first-run wizard
  user_config.py    local config loading/saving
  pipeline.py       shared transcription pipeline
  factory.py        downloader/provider assembly
  downloaders/      download implementations
  transcribers/     speech-to-text providers
  templates/        Jinja templates shared by web and desktop
  static/           shared local styles and scripts
  window_app.py     browser and pywebview launch, server lifecycle
tests/              automated tests
assets/             logos, screenshots, favicon, materials
archive/            preserved legacy files
```

## Local Setup

```bash
uv sync --extra whisper --extra web
```

For desktop verification, use `uv sync --extra whisper --extra window`. This extra includes the web dependencies.

Add more providers when needed:

```bash
uv sync --extra sensevoice --extra volcengine
```

It is usually safer to let Bootstrap manage combined environments:

```bash
uv run bili2text bootstrap
uv run bili2text bootstrap --sync-only
```

## Common Commands

```bash
uv run bili2text --help
uv run bili2text doctor
uv run bili2text bootstrap
uv run bili2text tx "<bilibili-url>"
uv run bili2text ui
uv run bili2text win
pytest -q
```

## Config

The default local config file lives at:

```text
./.b2t/config.json
```

Important fields:

- `language`
- `enabled_providers`
- `enabled_features`
- `default_provider`
- `default_model`
- `sensevoice.*`
- `volcengine.*`

## Provider Rules

When adding a provider:

- keep download logic out of the provider
- keep the provider focused on speech-to-text
- store config in `AppConfig`
- assemble dependencies in `factory.py`
- run real workflows through `pipeline.py`

## Feature Rules

`Window / Web / Server` should stay as thin shells:

- gather input
- call the shared pipeline
- present results
- avoid duplicating business logic

`web` and `window` must call the same `web.create_app` and `TaskService`. The desktop window loads local HTTP pages. Add UI behavior to the shared templates and `static/`.

Run the shared UI regression checks:

```bash
uv run --no-sync pytest tests/test_cli.py tests/test_web.py tests/test_window_app.py tests/test_bootstrap_env.py -q
```

Window tests use a real HTTP server and a substitute native driver. Verify the desktop GUI by launching it too. Transcription API tests use fixed results and do not validate models, downloads, or Bilibili account access.

## Commit Style

This refactor branch prefers small, frequent commits:

- one feature slice per commit
- one fix per commit
- split structural changes from behavior changes whenever possible
- run relevant tests before committing

## Cleanup Rules

- keep the repo root focused
- move historical scripts into `archive/`
- move visual/material assets into `assets/`
- keep `main.py` and `window.py` as compatibility entrypoints for now
