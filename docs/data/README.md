# Using the Edictai data

This page describes how the dataset is organized and how to query the built files. For
definitions of every field, see the [data dictionary](dictionary.md). For scope, sources, and
editorial rules, see the [methodology](../methodology.md).

## Canonical records

The source of truth is the YAML in `data/`, one record per file:

```
data/
  jurisdictions/{id}.yaml                                   us, us-ca, us-tx, ...
  organizations/{id}.yaml                                   us-ca-k12-agency, us-ny-suny, hlc, ...
  legislation/{jurisdiction}/{session}/{bill-slug}.yaml     us-tx/2025/hb-1481.yaml
  documents/{jurisdiction}/{slug}.yaml
  executive-actions/{jurisdiction}/{slug}.yaml
  research/{year}/{slug}.yaml
  schema/                                                   JSON Schema for each record type
  vocab/                                                    topics, provision types, enumerations
```

A record's `id` matches its path: `data/legislation/us-tx/2025/hb-1481.yaml` has the ID
`us-tx-2025-hb-1481`. IDs are permanent and safe to store as foreign keys in your own work.

## Built files

`make build` (or `uv run statescope build`) validates the records and writes them out as:

- **SQLite**: one database containing every table below, with derived fields such as
  legislative status already computed;
- **CSV**: one UTF-8 file per table, with a header row;
- **Parquet**: one file per table, with the same columns and typed values.

The same files are attached to the repository's
[GitHub releases](https://github.com/asiagenawi/statescope/releases). The SQL table
definitions are in [db/schema.sql](../../db/schema.sql).

### Tables

Records are normalized: list-valued fields in the YAML become their own tables, keyed by
the record ID.

| Table | One row per |
|---|---|
| `jurisdictions` | Country, state, or DC |
| `sessions` | Legislative session in a jurisdiction |
| `organizations` | Issuing body |
| `legislation` | Bill or resolution, including its derived `status` |
| `legislation_actions` | Dated action on a bill |
| `documents` | Policy document |
| `executive_actions` | Executive order or directive |
| `research` | Research item |
| `record_topics` | Topic assigned to a record |
| `record_education_levels` | Education level (`k12`, `postsecondary`) assigned to a record |
| `provisions` | Cited provision within a record |
| `sources` | Source cited by a record |
| `topics`, `provision_types` | Controlled vocabularies |

Tables that apply to several record types (`record_topics`, `record_education_levels`,
`provisions`, `sources`) carry `record_id` and `record_type`, where `record_type` is one of
`legislation`, `document`, `executive-action`, or `research`.

Each record table includes `verification_status`. Filter on it when you need only records
that have been checked against their source:

```sql
WHERE verification_status IN ('editor-reviewed', 'machine-verified')
```

## Example queries

Open the database with the `sqlite3` command-line tool, Datasette, DuckDB, or any SQLite
client.

### Enacted laws by state

```sql
SELECT j.name AS state, COUNT(*) AS enacted
FROM legislation l
JOIN jurisdictions j ON j.id = l.jurisdiction
WHERE l.status = 'enacted'
  AND j.classification IN ('state', 'district')
GROUP BY j.name
ORDER BY enacted DESC, state;
```

### Enacted K-12 laws on a topic

```sql
SELECT l.jurisdiction, l.identifier, l.session, l.title
FROM legislation l
JOIN record_topics t
  ON t.record_id = l.id AND t.record_type = 'legislation'
JOIN record_education_levels e
  ON e.record_id = l.id AND e.record_type = 'legislation'
WHERE l.status = 'enacted'
  AND t.topic = 'ai-literacy'
  AND e.education_level = 'k12'
ORDER BY l.jurisdiction, l.session;
```

### Provision grid: which states have enacted which provisions

One row per state and provision type, with the bills that contain it. Pivot the result in a
spreadsheet or dataframe to get a state-by-provision grid.

```sql
SELECT l.jurisdiction,
       p.type AS provision_type,
       GROUP_CONCAT(l.identifier, '; ') AS bills
FROM provisions p
JOIN legislation l
  ON l.id = p.record_id AND p.record_type = 'legislation'
WHERE l.status = 'enacted'
GROUP BY l.jurisdiction, p.type
ORDER BY l.jurisdiction, p.type;
```

A wide version, one column per provision type of interest:

```sql
SELECT j.abbreviation AS state,
       MAX(p.type = 'requires-ai-literacy-instruction') AS ai_literacy_instruction,
       MAX(p.type = 'requires-local-ai-policy')         AS local_ai_policy,
       MAX(p.type = 'issues-model-policy')              AS model_policy,
       MAX(p.type = 'restricts-student-data-use')       AS student_data,
       MAX(p.type = 'creates-task-force')               AS task_force
FROM jurisdictions j
LEFT JOIN legislation l
  ON l.jurisdiction = j.id AND l.status = 'enacted'
LEFT JOIN provisions p
  ON p.record_id = l.id AND p.record_type = 'legislation'
WHERE j.classification IN ('state', 'district')
GROUP BY j.abbreviation
ORDER BY j.abbreviation;
```

Values are `1` where an enacted bill contains the provision and `0` or empty otherwise. A
`0` means no such provision is recorded, not that the state has made a decision against it.

### Current state agency guidance

```sql
SELECT d.jurisdiction, o.name AS issuer, d.title, d.published, d.updated, d.version
FROM documents d
JOIN organizations o ON o.id = d.issuer
WHERE d.status = 'current'
  AND o.classification IN ('k12-agency', 'higher-ed-agency')
ORDER BY d.jurisdiction, COALESCE(d.updated, d.published);
```

### Higher-education system and accreditor policy

```sql
SELECT o.classification, o.name AS issuer, d.title, d.classification AS kind,
       COALESCE(d.updated, d.published) AS dated
FROM documents d
JOIN organizations o ON o.id = d.issuer
WHERE o.classification IN ('university-system', 'accreditor')
ORDER BY o.classification, o.name, dated;
```

### Legislative history of one bill

```sql
SELECT a.date, a.actor, group_concat(c.classification, ', ') AS classifications, a.description
FROM legislation_actions a
LEFT JOIN legislation_action_classifications c
  ON c.legislation_id = a.legislation_id AND c.seq = a.seq
WHERE a.legislation_id = 'us-tx-2025-hb-1481'
GROUP BY a.legislation_id, a.seq
ORDER BY a.date, a.seq;
```

### Bills introduced per year, by education level

```sql
SELECT substr(a.date, 1, 4) AS year, e.education_level, COUNT(DISTINCT l.id) AS bills
FROM legislation l
JOIN legislation_action_classifications c
  ON c.legislation_id = l.id AND c.classification = 'introduction'
JOIN legislation_actions a
  ON a.legislation_id = c.legislation_id AND a.seq = c.seq
JOIN record_education_levels e
  ON e.record_id = l.id AND e.record_type = 'legislation'
GROUP BY year, e.education_level
ORDER BY year, e.education_level;
```

### Sources without an archive copy

```sql
SELECT record_type, record_id, url
FROM sources
WHERE archived_url IS NULL
ORDER BY record_type, record_id;
```

## Reading the YAML directly

The records can also be read without the build. In Python:

```python
from pathlib import Path
import yaml

bills = [yaml.safe_load(p.read_text()) for p in Path("data/legislation").rglob("*.yaml")]
```

Note that legislation status is computed by the build and does not appear in the YAML.

## Attribution

The data is licensed under [CC BY 4.0](../../DATA_LICENSE.md). Please credit Edictai and
link to the license when you publish work that uses it.
