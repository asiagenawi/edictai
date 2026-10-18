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
"""
