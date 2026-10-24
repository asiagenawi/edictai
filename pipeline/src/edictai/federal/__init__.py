"""Find federal AI policy candidates from Congress.gov and the Federal Register.

The modules here fetch and normalize source data only. They write candidate
rows to `.cache/candidates/` and never create or edit records under `data/`.

- `congress`: Congress.gov API v3 client and bill normalization.
- `actions`: maps Congress.gov actions to the `action_classifications` vocabulary.
- `federal_register`: Federal Register API client and document normalization.
- `existing`: indexes the federal records already in `data/`.
- `run`: runs both searches, flags known items, and writes the output.
"""
