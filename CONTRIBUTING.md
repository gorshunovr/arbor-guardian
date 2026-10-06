# Contributing

Small stdlib-only Python project. A few notes so changes stay safe and reviewable.

## Setup

```sh
git clone https://github.com/gorshunovr/arbor-guardian.git
cd arbor-guardian
# optional: copy .env.example → .env for live portal use (gitignored)
```

Runtime needs **Python 3.10+** and the standard library only — no `pip install` to run the CLI.

Dev tooling (lint/format) is optional locally:

```sh
pip install 'ruff>=0.6'   # or: pip install -e '.[dev]' if using a venv
ruff check arbor_guardian arbor_guardian.py
ruff format arbor_guardian arbor_guardian.py
```

## Run

```sh
python3 arbor_guardian.py --help
python3 -m arbor_guardian children --help
```

Do **not** hit a live Arbor portal in CI or in shared logs. Prefer `--help`, `compileall`, and the tiny unit tests under `tests/`.

## Layout

| Path | Role |
|------|------|
| `arbor_guardian.py` | Thin CLI wrapper (keep this working) |
| `arbor_guardian/` | Package: auth, cache, CLI, one module per concern |
| `tests/` | Stdlib `unittest` for pure helpers (no network) |
| `.github/workflows/ci.yml` | GitHub Actions CI (source mirror: `ci/github-actions.yml`) |
| `notes/`, `har/`, `.env` | Local only — **gitignored**; never commit |

## Rules

- **No personal information** in commits (school names/subdomains, child names, student ids, emails, passwords).
- **No secrets** — credentials stay in the environment / a local `.env`.
- Prefer small, clear modules over clever abstractions.
- Keep the public UX: `python3 arbor_guardian.py …` must keep working.

## Pull requests

Prefer a feature branch + PR. GitHub Actions CI (`.github/workflows/ci.yml`) runs ruff (check + format), `compileall`, `--help` smoke, and unit tests on push and pull requests.
