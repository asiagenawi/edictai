"""Batch tooling for deciding which candidate bills are in scope and describing them.

The judgments themselves are written by reviewers (people or model agents)
working through batch files; this package prepares those batches, fetches
bill text, checks the judgments, turns accepted ones into records, and scores
them against the hand-labeled evaluation set.

Stages:

1. `prep --stage triage`: compact metadata batches; each bill is marked
   in, out, or needs_text.
2. `fetch-texts`: the latest bill text for every bill marked in or needs_text.
3. `prep --stage full`: batches with the AI-relevant parts of the text.
4. `import`: verified judgments become data/legislation records.
5. `score`: precision and recall against pipeline/eval/classifier_eval.yaml.

`run` does stages 1-3 through the Anthropic API with the same instructions,
schemas, and checks (see `run` and `llm`); `eval-gate` scores it on the
evaluation set (`evalgate`), `refresh` appends new actions to recorded bills
(`refresh`), and `summary` writes the weekly run's report (`summary`).
docs/automation.md describes the weekly workflow that runs them.

Federal candidates (`statescope federal` output: Congress.gov bills and
Federal Register documents) go through the same stages with `--federal` or a
federal `--candidates` file. They keep their own batches and judgments under
`.cache/classify/federal/` and texts under `.cache/texts/federal/`, take their
text from Congress.gov and the Federal Register, and become legislation,
executive-action, or document records (see `federal`, `federal_texts`, and
`federal_import`).
"""
