# Edictai

Edictai is an open dataset of artificial intelligence policy in US education, covering
K-12 and higher education in all 50 states, the District of Columbia, and the federal
government. Every record is a plain-text YAML file in this repository, checked against a
published schema and tied to the official source it was drawn from.

The site at [edictai.org](https://edictai.org) is
built from the same files.

## What the data covers

Edictai tracks policy that governs how AI is taught, used, procured, or regulated in
schools, colleges, and universities. It records what each policy says and where it came
from. It does not rate or rank states.

| Record type | What it holds | Location |
|---|---|---|
| Legislation | Bills and resolutions in state legislatures and Congress, with their dated legislative history | `data/legislation/` |
| Policy documents | Guidance, frameworks, binding policy, toolkits, and reports from state education agencies, higher-education agencies, public university systems, accreditors, and federal agencies | `data/documents/` |
| Executive actions | Executive orders and directives from governors and the President | `data/executive-actions/` |
| Research | Studies, reports, briefs, and surveys on AI in education | `data/research/` |
| Organizations | The bodies that issue policy: legislatures, governors' offices, agencies, university systems, accreditors | `data/organizations/` |
| Jurisdictions | The United States, the 50 states, and DC, with their legislative sessions | `data/jurisdictions/` |

Higher education is covered at the system level: state legislation, state coordinating and
governing boards, public university and college systems, accreditors, and federal action.
Policies set by individual campuses are out of scope.

Policies are tagged with subject **topics** (`data/vocab/topics.yaml`) and, where the text
supports it, with **provisions**: individual clauses classified by what they do, such as
requiring AI literacy instruction or requiring districts to adopt a local AI policy
(`data/vocab/provision-types.yaml`). Each provision carries a citation to the section of the
source that contains it.

See [docs/methodology.md](docs/methodology.md) for the scope definition, sources, and
editorial rules.

## Using the data

**On the site.** Browse by state, filter by topic and education level, and follow each
record back to its official source.

**As files.** `make build` produces the dataset in three formats:

- a single SQLite database, suitable for ad hoc SQL or tools such as Datasette;
- one CSV file per table, for spreadsheets and statistical software;
- Parquet files with the same tables, for analytical tools such as DuckDB, pandas, or R.

Built files are also attached to the repository's
[GitHub releases](https://github.com/asiagenawi/edictai/releases).

**From the source.** The YAML files in `data/` are the canonical form of the dataset and can
be read directly with any YAML library.

[docs/data/README.md](docs/data/README.md) describes the tables and gives example queries.
Field definitions are in [docs/data/dictionary.md](docs/data/dictionary.md).

## How records are verified

Each record carries a `verification` block with one of three statuses:

| Status | Meaning |
|---|---|
| `editor-reviewed` | A human editor checked the record against its source. |
| `machine-verified` | The official source text was retrieved and checked, automatically, to support each field. |
| `unverified` | Not yet confirmed against an official source. Treat with caution. |

Every record cites at least one source, and sources are archived with the Wayback Machine
where possible so that citations remain checkable if the original page moves. The status of
a bill is not entered by hand; it is derived from the bill's recorded actions when the
dataset is built.

## Repository layout

```
data/         Canonical records (YAML), schemas, and controlled vocabularies
  schema/     JSON Schema (draft 2020-12) for each record type
  vocab/      Topics, provision types, and enumerations
pipeline/     Python package: validation, build, and source ingestion
api/          Flask service behind the site's question-answering feature
site/         The website (Astro)
docs/         Methodology and data documentation
```

## Quick start for developers

Requirements: Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and Node.js 22 for the
site.

```bash
git clone https://github.com/asiagenawi/edictai.git
cd edictai
uv sync

make validate   # check every record against its schema and vocabularies
make build      # build SQLite, CSV, and Parquet outputs
make test       # run the test suite
make docs       # regenerate the data dictionary
```

The same commands are available through the command-line tool:

```bash
uv run edictai validate
uv run edictai build
```

To run the site locally:

```bash
cd site
npm ci
npm run dev
```

## Contributing

Corrections and additions are welcome. The quickest route is to open an issue using the
[correction](https://github.com/asiagenawi/edictai/issues/new?template=correction.yml) or
[missing policy](https://github.com/asiagenawi/edictai/issues/new?template=missing-policy.yml)
form. To edit records directly, see [CONTRIBUTING.md](CONTRIBUTING.md). Participation is
governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Citing Edictai

If you use the data, please cite it. GitHub's "Cite this repository" button uses
[CITATION.cff](CITATION.cff). A suggested form:

> Genawi, A. *Edictai: AI in Education Policy in the United States* [Data set].
> https://github.com/asiagenawi/edictai

Please include the date you accessed the data or the release you used, since records change
as policies move.

## License

- Code (everything outside `data/`, including `pipeline/`, `api/`, and `site/`) is released
  under the [MIT License](LICENSE).
- Data (everything in `data/` and all files built from it) is released under
  [Creative Commons Attribution 4.0 International](DATA_LICENSE.md).

Policy texts themselves belong to their issuing bodies. Edictai's summaries,
classifications, and structure are its own; links and short quotations are provided for
verification.
