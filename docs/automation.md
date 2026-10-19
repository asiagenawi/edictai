# Weekly update

`.github/workflows/weekly.yml` runs every Monday at 09:43 UTC and on demand
(Actions → Weekly update → Run workflow, with an optional dollar cap). One run
at a time; a run that starts while another is going waits for it.

It finds new candidate bills and federal documents, classifies them with the
Anthropic API, appends new actions to bills already recorded, writes one
article for the Articles section from the updated data, checks everything, and
publishes the changes: it opens a pull request, merges it into
`master` once every check has passed, and starts the site deploy. When a step
fails, nothing is merged; it opens or comments on one issue instead.

## Steps

| Step | Command | What it does |
|---|---|---|
| Restore the cache | `actions/cache/restore` | Restores `.cache/` (LegiScan, Congress.gov, Federal Register, candidates, classify, texts, articles) from the last run. |
| Baseline | `statescope classify baseline --if-new` | Only when the cache holds no judgments and no baseline: records the candidates already listed so they are not classified (see [Baseline](#baseline)). |
| LegiScan datasets | `statescope ingest legiscan` | Downloads datasets whose `dataset_hash` changed. |
| State candidates | `statescope ingest candidates` | Writes `.cache/candidates/YYYY-MM-DD.jsonl`. |
| Federal candidates | `statescope ingest federal --titles` | Writes `.cache/candidates/federal-YYYY-MM-DD.jsonl`, with every recorded federal bill's actions. |
| Research discovery | `statescope ingest research` | Disabled (`if: false`); the command fetches nothing. |
| Status of recorded bills | `statescope classify refresh` | Appends new actions and versions to existing bill records. |
| Evaluation gate | `statescope classify eval-gate` | Fails the run when the classifier scores below the floors. |
| Classify new candidates | `statescope classify run` | Triage, text fetching, and full judgments for state and federal candidates. |
| Import | `statescope classify import --strict-quotes` | Turns the in-scope judgments into records. |
| Validate records | `statescope validate --strict` | |
| Pipeline tests | `pytest` | |
| Build | `statescope build --no-parquet` | |
| API tests | `pytest api/tests` | In a separate virtual environment from `api/requirements-dev.txt`, after the build so the built-database tests read this week's data. |
| Node | `actions/setup-node` | Node 22, for the site build that gates the article. Allowed to fail. |
| Site dependencies | `npm ci` in `site/` | Allowed to fail. |
| Article | `statescope articles write` | Writes at most one article into `site/src/content/articles/` (see [Article](#article-articles-write)). Allowed to fail: when it does, or skips, the data changes go ahead without an article. |
| Summary | `statescope classify summary` | Writes `weekly/summary.md` and `weekly/weekly-result.json`. |
| Open or update the pull request | `peter-evans/create-pull-request` | Only when `data/` changed or an article was written, every step above passed, and the run is on `master`. Branch `automation/weekly-update`, base `master`, changes under `data/` and `site/src/content/articles/` only, body from `weekly/summary.md`. |
| Merge the pull request and deploy the site | `gh pr merge`, `gh workflow run` | Merges the pull request (squash) when it was opened, then runs `gh workflow run deploy-frontend.yml`. |
| Upload the run's files | `actions/upload-artifact` | The `weekly/` folder (run result, refresh, evaluation, spending, article result, summary), kept 30 days. Always runs. |
| Save the cache | `actions/cache/save` | Always runs, failed runs included, so the LegiScan monthly query counter is never lost. |
| Open or update the failure issue | `gh issue` | Only when a step failed: comments on the open issue labeled `weekly-update-failed`, or opens one, with the failed step, what the run did before it, and a link to the run. |

Steps after a failed step do not run, so nothing is imported once the
evaluation gate fails, and no pull request is opened once a check fails.

Secrets: `ANTHROPIC_API_KEY`, `LEGISCAN_API_KEY`, `CONGRESS_API_KEY`. Each is
passed only to the steps that use it. Variable: `WEEKLY_SPEND_CAP_USD`
(optional; default 10).

## Settings

`pipeline/automation.yaml`:

| Setting | Default | Meaning |
|---|---|---|
| `classify.model` | `claude-sonnet-5-5` | Model for every request. |
| `classify.effort` | `medium` | `output_config.effort`. |
| `classify.items_per_request` | triage 25, full 1 | Batch lines sent in one request. |
| `classify.max_tokens` | triage 12000, full 8000 | Output cap per request. |
| `classify.use_batches` | `true` | Message Batches API first; direct calls as the fallback. |
| `classify.batch_poll_seconds`, `batch_timeout_minutes` | 30, 90 | Polling interval; a batch still running at the timeout is cancelled. |
| `classify.direct_workers` | 4 | Direct calls in parallel. |
| `classify.text_version` | `ai` | `fetch-texts --version`. |
| `budget.spend_cap_usd` | 10 | Dollar cap per run; `WEEKLY_SPEND_CAP_USD` overrides it. |
| `budget.legiscan_text_queries` | 500 | getBillText queries per run at most. |
| `budget.legiscan_monthly_reserve` | 500 | LegiScan queries left unused each month. |
| `budget.federal_text_requests` | 300 | Congress.gov and Federal Register text requests per run. |
| `eval.precision_floor`, `eval.recall_floor` | 0.85, 0.9 | Evaluation floors. |
| `prices` | per model | USD per million tokens (input, output, cache write, cache read). |
| `batch_discount` | 0.5 | Batch requests cost this fraction of the standard price. |
| `articles.model` | `claude-opus-5-5` | Model for the article's drafting and editing calls. |
| `articles.draft_effort`, `edit_effort` | `high`, `high` | `output_config.effort` for each call. |
| `articles.max_tokens` | draft 32000, edit 32000 | Output cap per call (both are streamed). |
| `articles.spend_cap_usd` | 3 | Dollar cap for the article, inside the run's cap. |
| `articles.min_words`, `max_words`, `target_words` | 600, 1200, 850 | Words in the body paragraphs: the allowed range, and what the draft aims for. |
| `articles.window_days` | 7 | Activity dated this many days before the run date counts as changed this week. |
| `articles.max_records` | 60 | Records given to the model at most. |
| `articles.repair_attempts` | 1 | Further editing calls when the edited article fails the checks. |
| `articles.build_timeout_minutes` | 15 | Time allowed for the site build. |
| `articles.subjects` | 6, 4, 4, 6, 28 | `min_records`, `min_jurisdictions`, `min_studies`, `min_roundup_items`, `roundup_days` (see [Subject](#subject)). |

`classify run` and `classify eval-gate` also take `--model`, `--effort`,
`--spend-cap`, `--spend-file`, and `--no-batches`. `articles write` takes
`--model`, `--spend-cap`, `--article-cap`, and `--spend-file`.

## Classification (`classify run`)

For the state working directory (`.cache/classify/`) and then the federal one
(`.cache/classify/federal/`):

1. `TRIAGE.md`, `FULL.md`, and their schemas are regenerated, as `classify prep`
   does.
2. Candidates in the newest candidate file that have no record, no triage
   judgment, and no triage batch, and are not in `baseline.json` or
   `dropped.json`, are batched for triage, most recently active first.
3. Every triage batch without an output file is judged.
4. Bills triaged `in` or `needs_text` that have no full judgment get their text
   when none is stored (`fetch-texts`), then are batched for the full stage.
5. Every full batch without an output file is judged.

Each request has the stage's instruction file as its system prompt (cached
with `cache_control`), the batch lines as the user message with a short
wrapper saying to reply with `{"judgments": [...]}`, and the stage's judgment
schema as the structured output (`output_config.format`). The schema sent is
the one in `triage.schema.json` or `full.schema.json` without the keywords
structured outputs do not take (`minLength`, `maxLength`, `uniqueItems`,
`minItems`, `if`/`then`, and the like); `check` enforces all of them on the
reply.

Every judgment goes through the checks of `classify check` (schema,
vocabulary, word and sentence limits, neutral wording, verbatim quotes against
the stored text). An item with errors, or missing from the reply, is asked
once more on its own with the errors and its previous judgment listed. An item
that fails again is dropped: it is left out of the output and recorded in
`dropped.json` with its errors, and later runs skip it (`--retry-dropped`
clears the list).

Judgments that pass are written to `{stage}/out/batch-NNN.jsonl`, and
`batch-NNN.jsonl` is rewritten to the items judged, so `classify check` on an
output passes and `classify import` reads it as it reads a hand-written one.
Items the spending cap did not cover are taken out of their batch; the next run
batches them again.

Re-running is safe: candidates already judged or batched are not sent again.

### Baseline

`baseline.json` lists candidate keys the run never classifies.
`classify baseline` adds every unjudged candidate in the newest candidate
files; with `--if-new` it does so only when the working directory has no
triage judgments and no baseline. In the workflow it runs before the ingest
steps, so on a first run with an empty cache the candidates from earlier
ingests are recorded and only candidates new this week are classified.

### Imports

`auto-outputs.json` lists every full-stage output `classify run` has written.
The workflow imports all of them each week (`import_files` in the run result);
records already in `data/` are skipped, so a record from a run that did not
merge is imported again into the next one. Hand-written outputs are never
imported by the workflow.

### Spending cap

The cap covers every request of a run: `--spend-file weekly/spend.json` makes
the evaluation gate and the classification share it. Before a request is sent,
its worst-case cost is reserved: the system prompt and items at one token per
three characters, priced at the cache-write rate, plus `max_tokens` of output
(at the batch price for a batch request). A request whose reservation does not
fit is not sent. When the API returns, the reservation is replaced by the cost
of the usage it reports (input, output, cache writes, cache reads) at the
prices in the settings file. Batches are sent in waves sized to what remains,
so actual costs below the worst case make room for the next wave. A cancelled
batch that does not end within ten minutes keeps its reservations counted as
spent.

### Bill texts and LegiScan

State texts come from getBillText through `classify fetch-texts`, which reads
cached responses first and never requests a document twice. A run sends at
most `legiscan_text_queries` queries, and none once the month's count (kept in
`.cache/legiscan/usage.json`) reaches the monthly limit less
`legiscan_monthly_reserve`. The client refuses any query past the monthly
limit. Federal texts come from Congress.gov and the Federal Register through
the same command's federal path. No legiscan.com page is fetched.

## Status of recorded bills (`classify refresh`)

State records with `external_ids.legiscan_bill_id` are compared with the bill
in the cached LegiScan data (the session dataset, or a cached getBill response
when its history is newer). Federal records with `external_ids.congress_gov`,
or federal records without a LegiScan id, are compared with their row in the
newest federal candidate file. No request is sent.

Source actions are mapped as `classify import` maps them. An action is added
when it is dated after the record's last action (and not after today), or on
that date when every action the record has for that date appears in the
source. Versions are added only when every version the record lists appears in
the source. New items are appended to the end of the record's `actions` and
`versions` lists; no other line of the file changes. A record whose lists are
not in the block style is skipped and reported. Status is derived from actions
when the dataset is built, so the new actions move it.

## Evaluation gate (`classify eval-gate`)

The gate's key is a SHA-256 of the state and federal `TRIAGE.md`, `FULL.md`,
and both schemas, the request wrapper text, the model, and the effort. Results
are stored by key in `.cache/classify/eval-gate.json`. When the key has a
stored result, the gate uses it; otherwise (or with `--force`) it classifies
every entry of `pipeline/eval/classifier_eval.yaml` found among the current
candidates, records included, in its own working directory under
`.cache/classify/eval/`, with stored texts only, and scores the final calls
(full judgment, else triage) with `classify score`'s matching. Only a complete
evaluation is stored. The gate fails, and the run with it, when precision or
recall is below its floor or the evaluation did not finish within the cap.

## Article (`articles write`)

Each run writes at most one article, after the data is built, so it reads this
week's data from `build/statescope.db` (the build the site reads). Byline
Edictai (no `author`), `published` the run date, `data_release` the run's month
and year ("October 2026"). Opinion is never written: it is for outside
contributors.

`sketch_note` (a few handwritten words for the index drawing) comes from the
model and must not be empty. `drawing` is chosen from the subject: `calendar`
for a Roundup; `desk` (else `classroom`) for a Research digest; `gavel` (else
`map`) for a state; for a provision type, `capitol` when 60% or more of its
records are federal, then the type's scene (`phone` for deepfakes and chatbots,
`lock` for student data and procurement, `classroom` for teaching, educators,
literacy, standards, and integrity; or the scene of its records' top topics),
then `map`, then `gavel`. The first that differs from the drawings of the two
newest articles is used (an article without `drawing` counts as the one the
site gives it).

### Subject

A subject is a state (`jurisdiction:us-xx`, for a State brief), a provision
type across states (`provision:<code>`, for an Explainer or an Analysis), a
research topic (`research:<topic>`, for a Research digest), or a period
(`roundup:<from>..<to>`, for a Roundup).

Left out are subjects already covered: those the ledger records as written, and
those inferred from the existing articles' notes. An article whose record notes
mostly (60% or more) cite one jurisdiction covers that jurisdiction; otherwise
it covers the provision type its notes cite most. A Research digest covers the
topic its studies share most, and its studies count as cited. A Roundup covers
the days up to the day before it was published.

A subject needs `min_records` records (with provisions, for a state) and, for a
provision type, records in `min_jurisdictions` jurisdictions. A research topic
needs `min_studies` studies no article cites. A Roundup runs from the day after
the last one ended to the day before the run and needs `min_roundup_items`
records with dated activity in it; it counts as touching this week's changes
only once `roundup_days` have passed since the last one, so in a light week it
competes with the other types on rotation alone.

Records count as changed this week when git reports their files as added or
changed under `data/`, or when they have an action, publication, update, or
signature dated within `window_days` of the run date. The subject is chosen in
this order: subjects with changed records first; then the article type used
least recently (by the ledger and the existing articles' `published` dates);
then the most changed records; then the most records. `--type` and `--subject`
choose instead.

### What the model is given

One JSON document: the subject; a facts block of counts over every record in the
subject (by kind, legislation by status, enacted by year, by jurisdiction, by
education level, jurisdictions with an enacted law or current document, studies
by classification, the period) with the as-of date; the subject's records, up to
`max_records` (changed this week first, then enacted or current, then the most
recently active), each with its provisions (index, type, description, locator,
verbatim quote) and latest actions (all actions in the period, for a Roundup);
the subject's studies with their findings (index, finding, locator, quote) and
whether an article already cites them; and every existing article's slug, type,
title, dek, and date.

### Drafting and editing

Two separate calls through the classifier's `Judge`, each a streamed direct
call with structured output (`output_config.format`) whose schema is the
article's shape: slug, title, dek, brief, caption, sketch note, body blocks
(`p`, `h`, `table`), and notes (`record` and `provision`, or `study` and
`finding`).

1. The draft call's system prompt is the house style guide (written from the
   published articles: neutral, facts first, short paragraphs, no hype words,
   stock openings, rhetorical questions or conclusions, counts from the facts
   block with an as-of month, a note marker on every factual sentence, quotation
   marks only around words copied from a quote in the data), what the data
   fields mean, and the length range. The user message is the data.
2. The edit call is a fresh request: the editor's instructions and the same
   style guide as the system prompt, and the draft with only the records and
   studies its notes and table cite, plus the facts block. It checks every
   sentence against its note, fixes or removes what the data does not support,
   corrects counts, mood and note numbering, tightens the prose, and lists its
   changes (`edits`).
3. The edited article is checked. When it fails, the editor is called again
   (`repair_attempts` times) with the failures listed.

### Checks

The article is written only if it passes all of these:

- What the site's content schema (`site/src/content.config.ts`) enforces: the
  fields and their types; every note resolves to a record's provision or a
  study's key finding; every `[n]` in the text has a note and every note is
  cited; table rows fit their columns and record cells name records that exist
  and have an identifier or a text. Note markers outside paragraphs are refused.
- Notes and table cells cite only records and studies the model was given.
- Words in quotation marks appear in a provision quote, finding quote, or title
  given.
- Every number in the prose and the table appears in the data given (or the run
  date).
- The body paragraphs run `min_words` to `max_words` words; the dek is under 60
  words and the title about 12.
- No question marks, em dashes, exclamation marks, or listed hype and filler
  words.
- The type is the subject's, `published` the run date, the slug lowercase words
  joined by hyphens and not used by another article.
- The site builds with the file in place (`npm run build` in `site/`, with
  `EDICTAI_BUILD_DIR` set to this repository's `build/`), within
  `build_timeout_minutes`. On failure the file is removed.

The seven published articles pass the same checks (`test_articles.py`).

### Spending

The article has its own cap (`articles.spend_cap_usd`) inside the run's: a call
is sent only when its worst-case cost fits both, and what it costs is counted
in `weekly/spend.json` with the classifier's spending. A call the caps do not
cover is not sent and the article is skipped.

### Skipping

A failure at any point (no subject left, a missing build, the caps, an API
error or refusal, failed checks after the repair, a failed site build, or an
unexpected error) skips the article: nothing is written, the command still
exits 0, and the reason goes into `weekly/article.json`, the ledger, and the
pull-request summary. The data changes are unaffected.

### Ledger

`.cache/articles/ledger.json` (kept with the cache) has one entry per run date:
status (`written` or `skipped`), type, subject, period, slug, path, title,
reason, and spending. A rerun on the same date replaces the entry. When the
entry for the run date says written, nothing is sent: the file is kept, or, in a
fresh checkout, restored from `.cache/articles/written/<slug>.yaml` and built
again. Only written entries use a subject up.

## Outputs

`weekly/summary.md` (the pull-request body) lists records added (with
confidence, verification status, and source link), records updated (new
actions and the status before and after), items ruled out after reading the
text (confidence, reasons, link), how many were ruled out at triage, items
dropped, the article (headline, type, path, dek, word count, citations,
subject, spending, the editor's changes and the cited records and studies, or
why there is none this week), spending, and the evaluation result.

`weekly/weekly-result.json` holds the same facts: `status`, `failed_step`,
`changed`, `added`, `updated`, `other_changes`, `ruled_out`, `triaged_out`,
`dropped`, `not_sent`, `spend`, `eval`, and `article`. `changed` is true when
`data/` changed or an article was written.

`weekly/article.json` is the `articles write` result: `status` (`written`,
`exists`, `skipped`), `reason`, `subject`, `type`, `slug`, `path`, `title`,
`dek`, `words`, `citations`, `edits`, `errors` (the failed checks), `spend`, and
`model`.

## Running it locally

```sh
uv sync --extra classify
uv run statescope classify refresh --dry-run
uv run statescope classify eval-gate --spend-file weekly/spend.json --json weekly/eval-gate.json
uv run statescope classify run --spend-file weekly/spend.json --result weekly/run.json
uv run statescope build --no-parquet
uv run statescope articles write --spend-file weekly/spend.json --result weekly/article.json
uv run statescope classify summary --run weekly/run.json --article weekly/article.json \
  --markdown weekly/summary.md
```

`articles write --site DIR` writes into and builds a copy of the site instead
of `site/`, and `--ledger PATH` keeps a separate ledger, so a trial article
leaves the real ones alone. `--date` sets the run date, and `--no-build` skips
the site build.

`ANTHROPIC_API_KEY`, `LEGISCAN_API_KEY`, and `CONGRESS_API_KEY` are read from
the environment or `.env`. `classify run --scope state|federal` limits the run
to one working directory, `--limit N` to N new items per stage, and
`--no-fetch` skips text requests.
