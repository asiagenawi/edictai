# Weekly update

`.github/workflows/weekly.yml` runs every Monday at 09:43 UTC and on demand
(Actions → Weekly update → Run workflow, with an optional dollar cap). One run
at a time; a run that starts while another is going waits for it.

It finds new candidate bills and federal documents, classifies them with the
Anthropic API, appends new actions to bills already recorded, checks
everything, and opens or updates one pull request with the changes. It never
merges. When a step fails, it opens or comments on one issue instead.

## Steps

| Step | Command | What it does |
|---|---|---|
| Restore the cache | `actions/cache/restore` | Restores `.cache/` (LegiScan, Congress.gov, Federal Register, candidates, classify, texts) from the last run. |
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
| Summary | `statescope classify summary` | Writes `weekly/summary.md` and `weekly/weekly-result.json`. |
| Open or update the pull request | `peter-evans/create-pull-request` | Only when `data/` changed, every step above passed, and the run is on `master`. Branch `automation/weekly-update`, base `master`, changes under `data/` only, body from `weekly/summary.md`. |
| Upload the run's files | `actions/upload-artifact` | The `weekly/` folder (run result, refresh, evaluation, spending, summary), kept 30 days. Always runs. |
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

`classify run` and `classify eval-gate` also take `--model`, `--effort`,
`--spend-cap`, `--spend-file`, and `--no-batches`.

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
records already in `data/` are skipped, so a record from a pull request that was
not merged is imported again into the next one. Hand-written outputs are never
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

## Outputs

`weekly/summary.md` (the pull-request body) lists records added (with
confidence, verification status, and source link), records updated (new
actions and the status before and after), items ruled out after reading the
text (confidence, reasons, link), how many were ruled out at triage, items
dropped, spending, and the evaluation result.

`weekly/weekly-result.json` holds the same facts: `status`, `failed_step`,
`changed`, `added`, `updated`, `other_changes`, `ruled_out`, `triaged_out`,
`dropped`, `not_sent`, `spend`, and `eval`.

## Running it locally

```sh
uv sync --extra classify
uv run statescope classify refresh --dry-run
uv run statescope classify eval-gate --spend-file weekly/spend.json --json weekly/eval-gate.json
uv run statescope classify run --spend-file weekly/spend.json --result weekly/run.json
uv run statescope classify summary --run weekly/run.json --markdown weekly/summary.md
```

`ANTHROPIC_API_KEY`, `LEGISCAN_API_KEY`, and `CONGRESS_API_KEY` are read from
the environment or `.env`. `classify run --scope state|federal` limits the run
to one working directory, `--limit N` to N new items per stage, and
`--no-fetch` skips text requests.
