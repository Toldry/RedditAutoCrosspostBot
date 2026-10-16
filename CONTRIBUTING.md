# Contributing

Thanks for helping. This is the short version for people; [`AGENTS.md`](./AGENTS.md) is the full reference
(architecture, gotchas, conventions) and applies to human contributors too.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync                                  # Python 3.11 venv with all dependencies, dev tools included
uv run pybabel compile -d src/racb/locales   # only needed to run the bot outside Docker
uv run pytest                            # unit tests: mocked, no network, no database
```

`uv run pytest -m integration` posts **real** content as the bot accounts. Don't run it casually; CI runs it on
`master`.

## Pull requests

1. **Branch from `master`** and keep the PR to one topic.
2. **Title it as a [Conventional Commit](https://www.conventionalcommits.org/)** (`fix: ...`, `feat: ...`,
   `docs: ...`; add `!` for a breaking change). The commit titles decide the next version number.
3. **Add a changelog fragment** if users or deployers would notice the change: a new file in
   [`changelog.d/`](./changelog.d/), named like `+short-slug.fixed.md`. Format and types are in
   [`changelog.d/README.md`](./changelog.d/README.md). **Don't edit `CHANGELOG.md`** and **don't bump the
   version**: both happen automatically after merge.
4. **Update the docs you touched**: `AGENTS.md` describes the code in detail, so a behaviour change usually
   changes a line there too. If you change a bot reply, update every locale under
   [`src/racb/locales/`](./src/racb/locales/).
5. **Make sure `uv run pytest` passes.** CI runs the same.

Dependencies are locked in `uv.lock`; change them only with `uv add` / `uv remove` / `uv lock` and commit both
files.
