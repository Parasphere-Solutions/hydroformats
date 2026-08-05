# Contributing

Thanks for helping. The bar is simple and firm.

## Setup

```console
$ git clone https://github.com/Parasphere-Solutions/hydroformats
$ cd hydroformats
$ uv sync --extra dev
$ uv run pytest && uv run ruff check .
```

## The one rule that is the project

**Format claims require a citable public source.** A PR that adds or
changes a record layout must update `docs/FORMAT-SOURCES.md` with the
anchor (manual page, federal metadata, open-source reader). No anchor, no
merge — the record stays `UnknownRecord`. "It worked on my file" is a great
motivation and an insufficient citation.

## PR expectations

- Tests with every change; malformed-input cases for every parser change.
- `uv run pytest` and `uv run ruff check .` green.
- Keep the core dependency-free (stdlib only).
- Records are frozen dataclasses; parsers degrade, never raise, on content.
- Small, focused PRs beat large ones.

## Reporting format gaps

Open an issue with: the record tag, a sample line (sanitized is fine), what
logged it (software + version if known), and any documentation you have.
Real-world `.RAW`/`.HSX` oddities are exactly what we want to hear about.
