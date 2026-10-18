# Classifier evaluation set

`classifier_eval.yaml` is a hand-labeled set of real state and federal bills
(2023–2026 sessions, 118th–119th Congress) used to measure the precision and
recall of the bill-scope classifier. Every bill the ingest step pulls from
LegiScan's keyword search ("artificial intelligence", "AI", "machine
learning", "deepfake", "chatbot", "algorithm", and similar) is asked one
question: is this bill in scope for Edictai? This set is the answer key.

## Scope definition

**In scope**

- Legislation whose provisions substantively govern AI in K-12 or
  postsecondary education; or
- General AI legislation with a provision that specifically applies to
  schools, students, educators, or higher-education institutions. Examples:
  a deepfake law with a school-discipline section; a chatbot-and-minors law
  that places obligations on schools; a workforce bill that creates AI
  programs at community colleges.

**Out of scope**

- AI bills with no education provision.
- Education bills that mention technology but not AI.
- Bills where "AI" means something else (artificial insemination, other
  acronyms).
- Purely ceremonial resolutions, unless they direct an education action.

The borderline-case rules in `docs/methodology.md` (Scope → Borderline cases) are part of this definition, and labels follow them.

## How labels were decided

- Every entry was checked against the bill's official page or text
  (state legislature site, congress.gov, govinfo.gov). Bills that could not
  be confirmed on an official source were left out.
- The label is decided by the bill text, not its title. Each `rationale`
  names the section, clause, or defined term that decided it.
- A bill is in scope if any section of the version checked meets the
  definition. Minors or youth alone are not enough: the provision must
  name schools, students, educators, or institutions of higher education.
- `difficulty: clear` means the title or summary settles it.
  `difficulty: borderline` means the label depends on reading the text: an
  education section inside a general bill, an AI-and-minors bill with no
  school provision, education technology without AI, or general government
  AI use.
- Companion bills (House and Senate versions, or reintroductions in a later
  session) are separate entries, because the ingest step sees them
  separately.
- Out-of-scope keyword matches include text where "ai" is not artificial
  intelligence, such as the Hawaiian word *ai* and Iowa Code paragraph
  designators (`ai.`), alongside "artificial insemination".
- `education_levels` and `topics` (codes from `data/vocab/topics.yaml`) are
  recorded for in-scope entries only.

## Fields

| field | meaning |
|---|---|
| `id` | `{jurisdiction}-{session}-{bill-slug}`, same convention as `data/legislation/` |
| `jurisdiction` | `us` or `us-xx` |
| `session` | session identifier, same convention as `data/jurisdictions/`: the year for an annual session (`"2025"`); the range for a two-year legislature, including bills filed in its second year (`"2025-2026"`); the convening year plus an ordinal for a special session (`"2025-s1"`); the Congress number for federal bills (`"119"`) |
| `identifier` | bill number as written officially |
| `title` | official or short title |
| `url` | official bill page |
| `verified_url` | official page actually checked, when different from `url` |
| `label` | `in_scope` or `out_of_scope` |
| `difficulty` | `clear` or `borderline` |
| `education_levels` | in-scope only: `k12`, `postsecondary` |
| `topics` | in-scope only: topic codes |
| `matched_keywords` | search keyword(s) that would surface the bill; empty for a few education-technology bills included as negatives for broader searches |
| `rationale` | one sentence citing the text that decided the label |

## Using it

Report precision and recall on `in_scope`, overall and split by
`difficulty`. Errors on `borderline` entries are expected to be the
informative ones; errors on `clear` entries usually indicate a prompt or
parsing problem.
