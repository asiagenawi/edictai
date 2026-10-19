# Contributing to Edictai

Edictai is maintained in the open, and its accuracy depends on people who know a state,
an agency, or a bill well enough to notice when something is wrong or missing. This guide
covers the ways to help, from a one-line issue to a pull request.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Report a correction

If a record is wrong, out of date, or misleading, open a
[correction issue](https://github.com/asiagenawi/statescope/issues/new?template=correction.yml).
Include:

- the record ID or a link to the record on the site;
- what is wrong and what it should say;
- a link to an official source that supports the correction.

Corrections backed by an official source are prioritized. See
[Corrections policy](docs/methodology.md#corrections-policy) for how they are handled.

## Suggest a missing policy

If a bill, guidance document, executive order, system policy, or accreditor statement is
in scope but not in the dataset, open a
[missing policy issue](https://github.com/asiagenawi/statescope/issues/new?template=missing-policy.yml)
with the jurisdiction, the issuing body, and a link to the official text.
[docs/methodology.md](docs/methodology.md#scope) defines what is in scope.

## Edit a record by pull request

Each record is one YAML file. Paths and IDs are deterministic, and the ID must match the
file path.

| Record type | Path | ID |
|---|---|---|
| Legislation | `data/legislation/{jurisdiction}/{session}/{bill-slug}.yaml` | `{jurisdiction}-{session}-{bill-slug}` |
| Policy document | `data/documents/{jurisdiction}/{slug}.yaml` | `{jurisdiction}-{slug}` |
| Executive action | `data/executive-actions/{jurisdiction}/{slug}.yaml` | `{jurisdiction}-{slug}` |
| Research | `data/research/{year}/{slug}.yaml` | `{year}-{slug}` |
| Organization | `data/organizations/{id}.yaml` | `{id}` |
| Jurisdiction | `data/jurisdictions/{id}.yaml` | `us` or `us-{state}` |

Conventions:

- **Jurisdiction** is `us` for federal or `us-` plus the lowercase postal code, for example
  `us-tx`.
- **Session** is an identifier listed under `sessions` in the jurisdiction file, for
  example `2025` or `119` for the 119th Congress. Add the session there if it is missing.
- **Bill slug** is the lowercase chamber prefix and number, hyphenated: `hb-1481`, `sb-20`,
  `hr-610`, `s-321`, `hjr-5`. So Texas HB 1481 of 2025 lives at
  `data/legislation/us-tx/2025/hb-1481.yaml` with ID `us-tx-2025-hb-1481`.
- **Organization IDs** follow the pattern `{jurisdiction}-legislature`,
  `{jurisdiction}-governor`, `{jurisdiction}-k12-agency`, `{jurisdiction}-higher-ed-agency`,
  `{jurisdiction}-{system}` for university systems (`us-ca-csu`), and a short abbreviation
  for accreditors (`hlc`, `sacscoc`).
- **IDs are permanent.** Never rename or reuse a published ID. If a record was filed under
  the wrong ID, raise it in an issue.

### Schema and vocabularies

The schema for each record type is in `data/schema/`. Fields marked with `x-vocab` must use
a code from `data/vocab/`:

- `topics.yaml` for `topics`;
- `provision-types.yaml` for `provisions[].type`;
- `enums.yaml` for classifications, statuses, action types, source kinds, and verification
  statuses.

Fields marked with `x-ref` must point to an existing record, such as an `issuer` that exists
in `data/organizations/`.

Do not add a `status` field to legislation. Status is derived from the `actions` list at
build time, so record each dated action with its classification instead. Enter only actions
the legislature recorded; a bill that stalls is marked failed from its session's `end_date`,
so do not add a `died-at-session-end` action of your own.

For policy documents, `published` is the date first issued and `updated` the date of the
current edition, each only when the document or its issuer states it. Put an edition number
in `version`. When no date is given, leave both out and set `undated: true` rather than
estimating one from file metadata or archive captures.

### Validate before you push

```bash
uv sync
make validate        # or: uv run statescope validate
```

Validation checks every file against its schema, vocabularies, ID and path conventions, and
cross-references. Pull requests run the same checks in CI and cannot be merged until they
pass.

### Verification status on edited records

Set `verification.status` to `unverified` on any record you create or materially change,
unless you are a maintainer completing an editorial review. Describe what you checked in
`verification.notes`. Maintainers set `editor-reviewed` after review.

## Editorial standards

Records are held to the following standards. Pull requests that do not meet them will be
asked for changes.

**Official sources.** Every record cites at least one source with `kind: official`: the
legislature's bill page or text, the agency's own publication, the Federal Register, the
university system's policy page. News coverage and advocacy trackers are useful for finding
policies, but a record's facts must come from the official text. Secondary sources may be
listed in addition, with `kind: secondary`.

**Archive links.** Add an `archived_url` for each source, normally a Wayback Machine
snapshot (`https://web.archive.org/`). If no snapshot exists, request one with "Save Page
Now" before submitting. Record the date you retrieved the source in `accessed`.

**Neutral summaries.** Summaries are plain language, one to three sentences, and describe
what the policy does. Avoid characterizing it as strong, weak, landmark, harmful, or
overdue. Do not attribute motives. Quote the official digest in `abstract` when one exists.

**No advocacy in records.** Records describe; they do not argue. Opinions about whether a
policy is good belong elsewhere.

**Provisions need citations.** Add a provision only when you can point to the section,
page, or paragraph of the source that contains it, and include a short verbatim quote where
practical. Provision types record that a requirement exists; they are not a judgment of it.

**Accuracy over completeness.** If you cannot confirm a field from the source, leave it out.
An omitted field is better than a guessed one.

## Code contributions

The pipeline is a Python package managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # install dependencies, including development tools
uv run pre-commit install    # run formatting and lint checks on each commit
make test                    # or: uv run pytest
uv run ruff check .
uv run ruff format .
```

- Code is linted and formatted with [Ruff](https://docs.astral.sh/ruff/). Pre-commit hooks
  run Ruff and record validation before each commit.
- Add or update tests under the package's test directory for any change to validation,
  build, or ingestion behavior, and run `make test` before opening a pull request.
- If a change affects the schema or vocabularies, run `make docs` to regenerate
  `docs/data/dictionary.md` and include the result in the same pull request.
- Keep pull requests focused. Data changes and code changes are easier to review
  separately.

For the site, work in `site/` and run `npm run build` before submitting.

## Pull request review

All changes are merged by a maintainer after review. Data changes are checked against
their cited sources. Changes proposed by automated discovery go through the same review as
any other pull request.
