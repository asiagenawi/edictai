# Methodology

This document describes what Edictai records, where the information comes from, how it
is classified, and how it is checked and corrected.

## Scope

Edictai records **public policy on artificial intelligence in education** in the United
States: rules, requirements, guidance, and official actions that govern how AI is taught,
used, procured, or regulated in schools, colleges, and universities.

A policy is in scope when it is issued by a public or system-level body and either:

- addresses AI (including generative AI, machine learning, automated decision systems,
  chatbots, and AI-generated media) in an education setting, or
- is a broader AI measure with provisions that specifically apply to schools, students,
  educators, or institutions of higher education.

General AI legislation with no education-specific provisions is out of scope, as are
education technology or data-privacy measures that do not address AI.

### Borderline cases

These rules settle measures that sit near the edge of scope. A measure is judged on every
version it has had: if an earlier version met the definition and its AI provisions were
later amended out, it is still in scope, and its record says the AI language was removed.

| Situation | Treatment |
|---|---|
| General AI or automated-decision laws that list education among the decisions they regulate | In scope |
| AI councils or task forces with an education member but no duty to study education | Out of scope; in scope when the body must study or report on education |
| Chatbot, minors, or online-safety laws that mention schools only to exempt them | Out of scope |
| Funding a college or university for AI research, facilities, or hardware with no instruction or student training | Out of scope; in scope when it funds AI courses, programs, training, or support for education agencies |
| Bills that add AI to the definition of computer science | In scope when the change alters what must be taught; otherwise out |
| Federal research infrastructure with incidental eligibility for students or educators | Out of scope; fellowships and programs aimed at students or educators are in scope |
| Deepfake laws that let victims report to school staff without placing a duty on schools | Out of scope |
| Education-code measures whose AI provisions sit in a definition but have operative effect | In scope |
| Matches on "AI" that do not mean artificial intelligence | Out of scope |
| Resolutions that request a specific education agency to act | In scope; purely commemorative or honorary resolutions are out |
| Budget or appropriations items that fund AI work at one named campus | Out of scope; in scope when the money goes to a public college or university system, a state agency, or a statewide program |
| General AI or automated-decision laws whose only mention of education is in passing (for example, "education assistance" in a list of public benefits) with no duty on, or effect particular to, schools or colleges | Out of scope |
| AI weapon or threat detection for schools: grants, procurement criteria, studies, or requirements | In scope, under safety and wellbeing |

### Education levels

Each record is tagged `k12`, `postsecondary`, or both.

- **K-12** covers public elementary and secondary education: state legislation, state
  education agencies and boards of education, governors, and federal action affecting
  schools. Policies adopted by individual districts or schools are not recorded.
- **Higher education** is covered at the **system level**: state legislation, state
  higher-education coordinating and governing boards, public university and college systems,
  institutional and programmatic accreditors, and federal action. Policies of individual
  campuses, colleges, or departments are not recorded, even at large institutions.

### Jurisdictions

The 50 states, the District of Columbia, and the federal government. Each has a file in
`data/jurisdictions/` with its Open Civic Data identifier and the legislative sessions
referenced by legislation records. Regional accreditors that operate across states are
recorded as organizations without a jurisdiction.

## Record types

| Type | Covers |
|---|---|
| **Legislation** | Bills and resolutions introduced in a state legislature or Congress, including those that did not pass. Each carries its dated legislative history. |
| **Policy document** | Non-legislative policy from an agency, university system, or accreditor. Classified as guidance, binding policy, framework, toolkit, statement, report, model policy, or program. |
| **Executive action** | Executive orders, directives, proclamations, and memoranda from governors and the President. |
| **Research** | Studies, reports, briefs, surveys, datasets, and systematic reviews on AI in education, recorded for context. Research items are not policy and are not counted as such. |
| **Organization** | The bodies that issue policy, with the pages Edictai monitors for new or revised documents. |
| **Jurisdiction** | The country, states, and DC, with their sessions. |

Records link to one another where the relationship is documented: a guidance document can
name the bill that required it (`authorized_by`), a revised document points to its
replacement (`superseded_by`), and companion bills and related actions are listed in
`related`.

## Sources

Records are drawn from official sources wherever they exist:

| Source | Used for |
|---|---|
| State legislature websites | Bill text, versions, digests, sponsors, and legislative history |
| [Congress.gov](https://www.congress.gov/) | Federal bills, text, and actions |
| [Federal Register](https://www.federalregister.gov/) | Executive orders, federal rules, and notices |
| Governors' offices | State executive orders and directives |
| State education agencies and boards | K-12 guidance, frameworks, and policy |
| State higher-education agencies and boards | Postsecondary guidance and policy |
| Public university and college systems | System-wide policy, guidance, and task-force reports |
| Accreditors | Standards, policies, and statements on AI |
| Federal agencies | Federal guidance, such as from the U.S. Department of Education |
| [LegiScan](https://legiscan.com/) and [Open States](https://openstates.org/) | Discovery of state bills and normalized legislative histories, checked against the legislature's own record |

Aggregators, news coverage, and advocacy trackers are useful for finding policies but are not
relied on for facts. Each record cites at least one source of kind `official`; third-party
sources may be listed in addition with kind `secondary`.

Every source records the date it was retrieved (`accessed`) and, where possible, a permanent
archive copy (`archived_url`), normally from the Wayback Machine, so that a citation can be
checked even if the original page changes or is removed. For policy documents, observed
versions are recorded under `revisions`, with a hash of the extracted text so that changes
can be detected.

## Legislative status

The status of a bill is **not entered by hand**. Each legislation record lists the actions
taken on it, with dates and classifications modeled on the Open Civic Data action
vocabulary (`introduction`, `referral-committee`, `passage`, `executive-signature`, and so
on; the full list is in `data/vocab/enums.yaml`). The build derives the status from those
actions:

| Status | Derived when |
|---|---|
| `enacted` | The bill was signed, became law without signature, or a veto was overridden. |
| `vetoed` | The bill was vetoed and the veto was not overridden. |
| `failed` | The bill failed a vote, was withdrawn, or died at the end of its session. |
| `passed-legislature` | The bill passed both chambers and awaits executive action. |
| `passed-chamber` | The bill passed one chamber. |
| `in-committee` | The bill was referred to, heard in, or reported from committee. |
| `introduced` | The bill was introduced with no further recorded action. |

Statuses higher in the table take precedence. Deriving status from actions keeps it
consistent with the legislative record and means a status changes only when a new action is
recorded. The derivation is implemented in `pipeline/` and covered by tests.

Policy documents and executive actions carry an explicit status (`current`, `superseded`, or
`withdrawn` for documents; `active`, `rescinded`, `expired`, or `superseded` for executive
actions), taken from the issuing body's own record.

## Topics and provisions

Records are classified in two ways. Both are **descriptive, not evaluative**: they record
what a policy contains, not whether it is good policy. Edictai does not score, grade, or
rank states.

**Topics** (`data/vocab/topics.yaml`) describe subject matter, such as AI literacy, student
privacy, academic integrity, or procurement. A record may carry several.

**Provisions** (`data/vocab/provision-types.yaml`) describe what an individual clause does,
such as requiring AI literacy instruction, requiring districts to adopt a local AI policy,
restricting vendors' use of student data, or creating a task force. A provision is recorded
only with a citation to the part of the source that contains it: a URL, a locator such as
a section or page number, and where practical a short verbatim quotation. Provisions make it
possible to compare what states have done, clause by clause, without collapsing that
comparison into a single score.

Topic and provision codes are stable. A code is never renamed; if a category needs to change,
the old code is deprecated and a new one added.

## Summaries

Summaries are written in plain language for a general audience, in one to three sentences,
and describe what a policy does. They avoid characterization (strong, weak, landmark,
controversial) and do not attribute motives. When the issuing body publishes its own digest
or synopsis, it is quoted verbatim in a separate `abstract` field.

## Verification

Every record carries a verification status:

| Status | Meaning |
|---|---|
| `editor-reviewed` | A human editor checked the record against its source. |
| `machine-verified` | The official source text was retrieved and checked, automatically, to support each field. |
| `unverified` | Not yet confirmed against an official source. |

`editor-reviewed` and `machine-verified` records also record the date of the last check
(`last_verified`) and the method used. Notes on anything that could not be confirmed are kept
in `verification.notes`. Users who need a high-confidence subset can filter on these
statuses.

Fields that cannot be confirmed from a source are left out rather than estimated.

## Update process

The dataset is updated through pull requests to this repository, so every change is
reviewable and recorded in the git history.

- **Automated discovery** checks legislative data sources for new and updated bills and
  monitors the pages listed under `watch_urls` in each organization record for new or
  revised documents. Proposed additions and changes are opened as pull requests.
- **Validation** runs on every pull request: each record is checked against its JSON Schema,
  the controlled vocabularies, ID and path conventions, and cross-references between records.
- **Review.** A maintainer reviews each proposed change against its sources before merging.
  Automated changes receive the same review as contributions from people.
- **Build.** On merge, the dataset is rebuilt into SQLite, CSV, and Parquet and the site is
  regenerated.

## Corrections policy

Corrections are welcome from anyone. Report them with the
[correction form](https://github.com/asiagenawi/statescope/issues/new?template=correction.yml)
or by pull request (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

- A correction is accepted when it is supported by an official source.
- Corrections are made in the record itself, and the change, its reason, and its source are
  visible in the pull request and the git history. Records are not silently rewritten.
- A corrected record's verification status is updated to reflect the new check.
- Record IDs are permanent. A record found to be out of scope is removed in a reviewed pull
  request that states the reason; its ID is not reused.
- Policy positions or requests to change how a policy is characterized beyond what its text
  supports are not treated as corrections.
