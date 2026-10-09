# A2U

AI agents that talk to customers over web chat, web voice, phone and WhatsApp, with one brain and one customer memory across channels. Start with [CLAUDE.md](CLAUDE.md) and [docs/](docs/README.md).

## Local development

Needs `uv`, `bun` and Docker.

```sh
uv sync --all-packages                      # Python workspace (packages/, services/)
bun install                                 # TypeScript workspace (web/)
docker compose -f docker-compose.dev.yml up -d   # Postgres + pgvector on :5432
```

Checks, the same ones CI runs:

```sh
uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest
bun run lint && bun run typecheck && bun run test
```

`uv run pytest` also works inside any single package directory.
