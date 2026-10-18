## Summary

<!-- What this pull request changes, and why. Link any related issue, e.g. "Closes #12". -->

## Type of change

- [ ] Data: new record(s)
- [ ] Data: correction to existing record(s)
- [ ] Code, schema, or vocabulary
- [ ] Documentation

## Data checklist

<!-- For pull requests that add or change records in data/. Delete this section otherwise. -->

- [ ] `make validate` passes locally.
- [ ] Each record cites at least one official source (`kind: official`), and its facts come from that source.
- [ ] Each source has an `archived_url` (Wayback Machine or equivalent) and an `accessed` date.
- [ ] Summaries are neutral, plain language, and one to three sentences. No advocacy or characterization.
- [ ] Provisions cite a section, page, or short quotation from the source.
- [ ] File paths and IDs follow the conventions in CONTRIBUTING.md; no published ID was renamed.
- [ ] Legislation status is expressed through dated `actions`, not a `status` field.
- [ ] `verification.status` is `unverified` for new or materially changed records (unless set by a maintainer after review).

## Code checklist

<!-- For pull requests that change code, schemas, or vocabularies. Delete this section otherwise. -->

- [ ] `make test` passes.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `make docs` was run if the schema or vocabularies changed.
