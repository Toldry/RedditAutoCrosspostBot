# Changelog fragments

Pull requests never edit [`CHANGELOG.md`](../CHANGELOG.md). Each one adds a small file here instead, so two
open PRs never touch the same lines and merging one never conflicts with the others. After a merge to
`master`, the CI `release` job runs [towncrier](https://towncrier.readthedocs.io/), which turns every file here
into an entry of a new, dated CHANGELOG section and deletes the files.

## Naming

`+<slug>.<type>.md`, for example `+transparent-images.fixed.md`.

- `<slug>`: a few words in kebab-case, unique enough not to clash with another open PR.
- `<type>`: one of Keep a Changelog's sections:

  | Type | Use for |
  | :--- | :--- |
  | `added` | new features |
  | `changed` | changes in existing behaviour |
  | `deprecated` | features that will be removed |
  | `removed` | features that were removed |
  | `fixed` | bug fixes |
  | `security` | vulnerabilities |

- Instead of `+<slug>`, the PR number works too (`95.fixed.md`); towncrier then adds a link to the PR.
- A PR with two unrelated changes adds two files.

## Content

One paragraph of Markdown, without a leading `- ` (towncrier adds the bullet). Same style as the existing
CHANGELOG entries: a bold title, then what changed and why, for someone running or reading the bot.

```markdown
**Transparent Images in the Image Duplicate Scan:** a transparent picture was hashed by the hidden colour behind its see-through pixels, so two copies of the same image could fail to match. Transparent images are now laid over a white background before hashing.
```

Preview the next release: `uv run towncrier build --draft --version X.Y.Z`.
`uv run pytest` fails if towncrier cannot read a fragment (for example a misspelled type).

The version bump does not depend on the fragment type: the Conventional Commit titles decide it
(`feat` minor, `!` major, anything else patch). A merge that adds no fragment makes no release.
