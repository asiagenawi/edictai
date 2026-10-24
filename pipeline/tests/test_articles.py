"""`articles write`: subject choice, checks, budget, skipping, and the ledger, with the
Anthropic client faked and the site build replaced by a stub."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from edictai.articles.check import check_article, from_reply
from edictai.articles.context import build_context
from edictai.articles.data import Article, load_articles, load_dataset
from edictai.articles.subjects import (
    Limits,
    Subject,
    candidates,
    choose,
    coverage,
    read_ledger,
    rotation,
    write_ledger_entry,
)
from edictai.articles.write import ArticleSettings, write_article
from edictai.build import build
from edictai.classify.llm import Budget, CappedBudget
from edictai.classify.settings import Settings
from edictai.classify.summary import build_summary, markdown
from edictai.yamlio import load_yaml

from .conftest import REPO, make_tree

RUN = date(2026, 10, 19)
PRICES = {"claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2}}
LIMITS = Limits(min_records=1, min_jurisdictions=1, min_studies=1, min_roundup_items=1)
TX = "jurisdiction:us-tx"


@pytest.fixture
def repo(tmp_path):
    paths = make_tree(tmp_path / "repo")
    build(paths, today=date(2026, 10, 17), log=lambda _m: None)
    (paths.root / "site" / "src" / "content" / "articles" / "2026").mkdir(parents=True)
    return paths


def settings(**over):
    return Settings(prices=PRICES, spend_cap_usd=10.0, **over)


def asettings(**over):
    base = {"min_words": 20, "max_words": 400, "repair_attempts": 1, "limits": LIMITS}
    return ArticleSettings(**{**base, **over})


PARA = (
    "As of October 19, 2026, Edictai's records show that HB 1481, signed by the governor "
    "of Texas, requires each school district to adopt a policy on the use of artificial "
    "intelligence by its students and its teachers [1]."
)


def reply(slug="texas-ai-policy-laws", notes=None, paragraph=PARA, edits=True):
    out = {
        "slug": slug,
        "title": "What Texas requires of school districts on AI",
        "dek": "Texas law requires each school district to adopt an AI policy.",
        "brief": [
            "HB 1481 requires a district AI policy.",
            "The Texas Education Agency issued guidance.",
        ],
        "caption": "Texas records cited in this brief, as recorded by Edictai in October 2026.",
        "sketch_note": "a policy per district",
        "body": [
            {"p": paragraph},
            {"h": "Guidance"},
            {"p": "The Texas Education Agency also issued guidance for districts on AI [2]."},
        ],
        "notes": notes
        if notes is not None
        else [
            {"record": "us-tx-2025-hb-1481", "provision": 0},
            {"record": "us-tx-tea-ai-guidance", "provision": 0},
        ],
    }
    if edits:
        out["edits"] = ["Tightened the opening."]
    return out


def message(data, usage=(20_000, 3_000)):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(data))],
        usage=SimpleNamespace(
            input_tokens=usage[0],
            output_tokens=usage[1],
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


class Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    """`draft` and `edit(n)` give the reply for the draft and the n-th editing call."""

    def __init__(self, draft=None, edit=None):
        self.draft = draft or (lambda: reply(edits=False))
        self.edit = edit or (lambda n: reply())
        self.calls = []
        self.messages = SimpleNamespace(stream=self.stream, create=self.stream)

    def stream(self, **params):
        self.calls.append(params)
        system = params["system"][0]["text"]
        if system.startswith("You write articles"):
            return Stream(message(self.draft()))
        n = sum(1 for p in self.calls if not p["system"][0]["text"].startswith("You write"))
        return Stream(message(self.edit(n)))


def run(repo, client, *, budget=None, build_ok=True, a=None, **kw):
    builds = []

    def stub(site):
        builds.append(site)
        return (True, "ok") if build_ok else (False, "Note 3 cites record x")

    kw.setdefault("subject_key", TX)
    result = write_article(
        repo,
        settings(),
        a or asettings(),
        budget or Budget(10.0),
        run_date=RUN,
        site=repo.root / "site",
        ledger=repo.root / ".cache" / "articles" / "ledger.json",
        client=client,
        build=stub,
        **kw,
    )
    return result, builds


def article_path(repo, slug="texas-ai-policy-laws"):
    return repo.root / "site" / "src" / "content" / "articles" / "2026" / f"{slug}.yaml"


# -- subject choice --------------------------------------------------------------------


def write_existing(repo, notes, type="State brief", slug="old", published="2026-10-18"):
    path = repo.root / "site" / "src" / "content" / "articles" / "2026" / f"{slug}.yaml"
    path.write_text(
        yaml.safe_dump(
            {"title": "Old", "dek": "Old.", "type": type, "published": published, "notes": notes}
        ),
        encoding="utf-8",
    )


def test_subjects_skip_what_existing_articles_cover(repo):
    ds = load_dataset(repo.root / "build" / "edictai.db")
    content = repo.root / "site" / "src" / "content" / "articles"
    before = {
        s.key for s in candidates(ds, coverage(ds, [], []), set(), run_date=RUN, limits=LIMITS)
    }
    assert TX in before
    write_existing(repo, [{"record": "us-tx-2025-hb-1481"}, {"record": "us-tx-eo-ga-99"}])
    cover = coverage(ds, load_articles(content), [])
    after = {s.key for s in candidates(ds, cover, set(), run_date=RUN, limits=LIMITS)}
    assert TX in cover.keys and TX not in after


def test_subjects_skip_the_ledger_and_rotate_types(repo):
    ds = load_dataset(repo.root / "build" / "edictai.db")
    ledger = [
        {
            "run_date": "2026-10-12",
            "status": "written",
            "type": "Explainer",
            "subject": "provision:requires-local-ai-policy",
        },
        {"run_date": "2026-10-05", "status": "skipped", "type": "Analysis", "subject": TX},
    ]
    cover = coverage(ds, [], ledger)
    subjects = candidates(ds, cover, set(), run_date=RUN, limits=LIMITS)
    keys = {s.key for s in subjects}
    assert "provision:requires-local-ai-policy" not in keys
    assert TX in keys  # a skipped attempt does not use the subject up
    assert rotation(cover)[-1] == "Explainer"
    assert choose(subjects, cover).type != "Explainer"


def test_subjects_prefer_this_weeks_changes(repo):
    ds = load_dataset(repo.root / "build" / "edictai.db")
    cover = coverage(ds, [], [])
    changed = {"us-tx-2025-hb-1481"}
    pick = choose(candidates(ds, cover, changed, run_date=RUN, limits=LIMITS), cover)
    assert pick.changed > 0 and "us-tx-2025-hb-1481" in pick.records


# -- checks ------------------------------------------------------------------------------


def context(repo):
    ds = load_dataset(repo.root / "build" / "edictai.db")
    ids = ["us-tx-2025-hb-1481", "us-tx-tea-ai-guidance", "us-tx-eo-ga-99"]
    return build_context(
        ds, Subject(TX, "State brief", "Texas", ids), set(), [], run_date=RUN, max_records=60
    )


def errors_for(repo, r):
    slug, art = from_reply(r, type="State brief", published="2026-10-19", release="October 2026")
    return check_article(
        slug,
        art,
        context(repo),
        type="State brief",
        published="2026-10-19",
        existing_slugs=["old"],
        min_words=20,
        max_words=400,
    )


def test_a_good_article_passes(repo):
    assert errors_for(repo, reply()) == []


@pytest.mark.parametrize(
    ("notes", "expect"),
    [
        (
            [
                {"record": "us-ca-2025-ab-1", "provision": 0},
                {"record": "us-tx-tea-ai-guidance", "provision": 0},
            ],
            "not in the data given",
        ),
        (
            [
                {"record": "us-tx-2025-hb-1481", "provision": 7},
                {"record": "us-tx-tea-ai-guidance", "provision": 0},
            ],
            "has no provision 7",
        ),
        # In the data, but not given to the model for this subject.
        (
            [
                {"record": "us-119-hr-610", "provision": 0},
                {"record": "us-tx-tea-ai-guidance", "provision": 0},
            ],
            "not in the data given",
        ),
        ([{"record": "us-tx-2025-hb-1481", "provision": 0}], "there are 1 notes"),
    ],
)
def test_invalid_citations_are_rejected(repo, notes, expect):
    assert any(expect in e for e in errors_for(repo, reply(notes=notes)))


def test_quotes_and_numbers_must_come_from_the_data(repo):
    made_up = PARA.replace("requires", "says “every district must ban chatbots” and requires")
    assert any("quotes words not found" in e for e in errors_for(repo, reply(paragraph=made_up)))
    real = PARA.replace("requires", "says “Each school district shall adopt a policy” and requires")
    assert errors_for(repo, reply(paragraph=real)) == []
    stat = PARA.replace("[1].", "[1]. It covers 4,312 districts.")
    assert any("4312" in e for e in errors_for(repo, reply(paragraph=stat)))


def test_style_and_length_rules(repo):
    assert any("question" in e for e in errors_for(repo, reply(paragraph=PARA + " Why?")))
    assert any(
        "landmark" in e for e in errors_for(repo, reply(paragraph="A landmark law. " + PARA))
    )
    assert any("words" in e for e in errors_for(repo, reply(paragraph="Short [1].")))
    assert any("already used" in e for e in errors_for(repo, reply(slug="old")))


def test_checks_pass_on_the_published_articles():
    db = REPO / "build" / "edictai.db"
    content = REPO / "site" / "src" / "content" / "articles"
    if not db.exists() or not any(content.glob("**/*.yaml")):
        pytest.skip("needs a build of the repository's data")
    ds = load_dataset(db)
    for a in load_articles(content):
        data = load_yaml(a.path)
        subject = Subject("all", data["type"], "all", list(ds.records), list(ds.studies))
        ctx = build_context(ds, subject, set(), [], run_date=RUN, max_records=10_000)
        errors = check_article(
            a.slug,
            data,
            ctx,
            type=data["type"],
            published=data["published"],
            existing_slugs=[],
            min_words=600,
            max_words=1200,
        )
        assert errors == [], (a.slug, errors)


# -- the command ------------------------------------------------------------------------------


def test_writes_the_article_after_a_draft_and_an_edit(repo):
    client = FakeClient()
    result, builds = run(repo, client)
    assert result.status == "written", result.reason
    assert len(client.calls) == 2 and len(builds) == 1
    draft, edit = client.calls
    assert draft["model"] == "claude-opus-5-5" and draft["output_config"]["effort"] == "high"
    assert "us-tx-2025-hb-1481" in draft["messages"][0]["content"]
    # The editor gets the draft and the data its notes cite, not the whole subject.
    assert "us-tx-eo-ga-99" not in edit["messages"][0]["content"]
    assert "edits" in edit["output_config"]["format"]["schema"]["properties"]
    data = load_yaml(article_path(repo))
    assert data["type"] == "State brief" and data["published"] == "2026-10-19"
    assert data["data_release"] == "October 2026" and "author" not in data
    assert data["notes"] == [
        {"record": "us-tx-2025-hb-1481"},
        {"record": "us-tx-tea-ai-guidance"},
    ]
    assert result.spend["spent_usd"] > 0 and result.spend["cap_usd"] == 3.0
    assert (repo.cache / "articles" / "written" / "texas-ai-policy-laws.yaml").exists()


def test_a_failed_check_gets_one_repair(repo):
    bad = reply(
        notes=[
            {"record": "us-tx-2025-hb-1481", "provision": 9},
            {"record": "us-tx-tea-ai-guidance", "provision": 0},
        ]
    )
    client = FakeClient(edit=lambda n: bad if n == 1 else reply())
    result, _ = run(repo, client)
    assert result.status == "written"
    assert len(client.calls) == 3
    assert "has no provision 9" in client.calls[2]["messages"][0]["content"]


def test_invalid_citations_skip_the_article(repo):
    bad = reply(
        notes=[
            {"record": "us-ca-made-up", "provision": 0},
            {"record": "us-tx-tea-ai-guidance", "provision": 0},
        ]
    )
    client = FakeClient(edit=lambda n: bad)
    result, builds = run(repo, client)
    assert result.status == "skipped" and "not in the data given" in result.reason
    assert builds == [] and not article_path(repo).exists()
    assert len(client.calls) == 3  # draft, edit, one repair
    assert read_ledger(repo.cache / "articles" / "ledger.json")[0]["status"] == "skipped"


def test_a_failed_site_build_skips_the_article(repo):
    result, builds = run(repo, FakeClient(), build_ok=False)
    assert result.status == "skipped" and "site build failed" in result.reason
    assert len(builds) == 1 and not article_path(repo).exists()


def test_the_run_cap_stops_the_article(repo):
    client = FakeClient()
    budget = Budget(10.0)
    budget.spent = 9.95
    result, _ = run(repo, client, budget=budget)
    assert result.status == "skipped" and "spending cap" in result.reason
    assert client.calls == []


def test_the_article_cap_stops_the_editing_call(repo):
    client = FakeClient()
    budget = Budget(10.0)
    # The draft (about $0.64 reserved, $0.14 spent) fits; the edit does not.
    result, _ = run(repo, client, budget=budget, a=asettings(spend_cap_usd=0.75))
    assert (
        result.status == "skipped" and "edit" in result.reason and "spending cap" in result.reason
    )
    assert len(client.calls) == 1
    assert budget.spent == pytest.approx(result.spend["spent_usd"], abs=1e-3) and budget.spent > 0


def test_capped_budget_counts_against_the_run(tmp_path):
    parent = Budget(5.0, tmp_path / "spend.json")
    sub = CappedBudget(parent, 1.0)
    assert sub.reserve(0.8)
    assert not sub.reserve(0.3) and sub.stopped
    sub.settle(0.8, 0.5)
    assert parent.spent == 0.5 and sub.spent == 0.5 and parent.reserved == 0
    assert json.loads((tmp_path / "spend.json").read_text())["spent_usd"] == 0.5


def test_the_ledger_makes_a_rerun_send_nothing(repo):
    client = FakeClient()
    first, _ = run(repo, client)
    assert first.status == "written"
    again, builds = run(repo, client)
    assert again.status == "exists" and again.slug == first.slug and builds == []
    assert len(client.calls) == 2
    # A fresh checkout (the file gone, the cache kept) gets the article back.
    article_path(repo).unlink()
    restored, builds = run(repo, client)
    assert restored.status == "exists" and article_path(repo).exists() and len(builds) == 1
    assert len(client.calls) == 2
    ledger = read_ledger(repo.cache / "articles" / "ledger.json")
    assert [e["run_date"] for e in ledger] == ["2026-10-19"]


def test_ledger_entries_replace_their_run_date(tmp_path):
    path = tmp_path / "ledger.json"
    write_ledger_entry(path, {"run_date": "2026-10-19", "status": "skipped"})
    write_ledger_entry(path, {"run_date": "2026-10-12", "status": "written"})
    write_ledger_entry(path, {"run_date": "2026-10-19", "status": "written"})
    assert read_ledger(path) == [
        {"run_date": "2026-10-12", "status": "written"},
        {"run_date": "2026-10-19", "status": "written"},
    ]


def test_the_summary_reports_the_article(repo, tmp_path):
    result, _ = run(repo, FakeClient())
    out = tmp_path / "article.json"
    out.write_text(json.dumps({**result.as_dict(), "path": "site/" + result.path}))
    s = build_summary(repo, article=out, changes=([], []))
    assert s.changed
    body = markdown(s)
    assert "What Texas requires of school districts on AI" in body
    assert "State brief" in body and "2 notes citing 2 records" in body

    skipped = tmp_path / "skipped.json"
    skipped.write_text(json.dumps({"status": "skipped", "reason": "the site build failed"}))
    s = build_summary(repo, article=skipped, changes=([], []))
    assert not s.changed and "No article this week: the site build failed" in markdown(s)
    s = build_summary(repo, article=tmp_path / "missing.json", changes=([], []))
    assert "did not finish" in markdown(s)


def test_existing_articles_load_their_notes(tmp_path):
    path = tmp_path / "2026" / "x.yaml"
    path.parent.mkdir()
    path.write_text(
        "title: T\ndek: D\ntype: Analysis\npublished: '2026-10-18'\nnotes:\n"
        "  - record: a\n  - { record: b, provision: 2 }\n  - { study: s, finding: 1 }\n"
    )
    (a,) = load_articles(tmp_path)
    assert isinstance(a, Article)
    assert a.record_notes == [("a", 0), ("b", 2)] and a.study_notes == [("s", 1)]
    assert Path(a.path).name == "x.yaml"


def test_the_drawing_fits_the_subject_and_varies(repo):
    from edictai.articles.subjects import choose_drawing

    ds = load_dataset(repo.root / "build" / "edictai.db")
    state = Subject(TX, "State brief", "Texas", ["us-tx-2025-hb-1481"])
    assert choose_drawing(ds, state, []) == "gavel"
    recent = [
        Article("a", Path("a"), "", "", "Analysis", "2026-10-18", [], [], drawing="gavel"),
        Article("b", Path("b"), "", "", "Analysis", "2026-10-17", [], [], drawing="phone"),
        Article("c", Path("c"), "", "", "Analysis", "2026-10-01", [], [], drawing="map"),
    ]
    assert choose_drawing(ds, state, recent) == "map"
    policy = Subject("provision:requires-local-ai-policy", "Analysis", "x", ["us-tx-2025-hb-1481"])
    assert choose_drawing(ds, policy, recent) == "map"
    roundup = Subject("roundup:2026-10-01..2026-10-18", "Roundup", "x", window=("a", "b"))
    assert choose_drawing(ds, roundup, recent) == "calendar"


def test_the_written_article_names_its_drawing_and_sketch_note(repo):
    run(repo, FakeClient())
    data = load_yaml(article_path(repo))
    assert data["drawing"] == "gavel" and data["sketch_note"] == "a policy per district"
    empty = reply()
    empty["sketch_note"] = ""
    assert any("sketch_note" in e for e in errors_for(repo, empty))


# -- subject scoring -----------------------------------------------------------------------


def _rec(rid, juris, status, code, *, day="2024-03-01", kind="legislation"):
    from edictai.articles.data import Provision, Record

    dates = {"legislation": {"enacted_date": day} if status == "enacted" else {}}
    dates = dates.get(kind) or ({"published": day} if kind == "document" else {})
    return Record(
        id=rid,
        type=kind,
        jurisdiction=juris,
        jurisdiction_name=juris,
        identifier=rid.upper() if kind == "legislation" else None,
        title=rid,
        classification="bill",
        status=status,
        summary="",
        path="",
        dates=dates,
        provisions=[Provision(0, code, "d", None, None)],
        actions=[{"date": day, "description": "Action"}],
    )


def _dataset(records):
    from edictai.articles.data import Dataset

    return Dataset({r.id: r for r in records}, {}, {}, {}, {}, {})


STATES = ("us-aa", "us-bb", "us-cc", "us-dd", "us-ee")
SMALL = Limits(min_records=4, min_jurisdictions=4, min_studies=1, min_roundup_items=99)


def _funding_and_disclosure():
    """A large broad type with old laws, and a small type several states just enacted."""
    big = [
        _rec(
            f"fund-{i}",
            STATES[i % 5],
            "enacted" if i < 10 else "in-committee",
            "appropriates-funding",
        )
        for i in range(30)
    ]
    small = [
        _rec(f"disc-{i}", STATES[i], "enacted", "requires-disclosure", day="2026-09-20")
        for i in range(4)
    ]
    return big + small


def test_recent_laws_beat_size_and_broad_types_rank_low():
    ds = _dataset(_funding_and_disclosure())
    cover = coverage(ds, [], [])
    subjects = candidates(ds, cover, set(), run_date=RUN, limits=SMALL)
    funding = next(s for s in subjects if s.key == "provision:appropriates-funding")
    disclosure = next(s for s in subjects if s.key == "provision:requires-disclosure")
    assert disclosure.score > funding.score
    assert funding.why["broad provision type"] < 0
    assert disclosure.why["enacted or issued recently"] > 0
    assert choose(subjects, cover).key == "provision:requires-disclosure"


def test_a_broad_type_counts_fully_when_it_changed_this_week():
    ds = _dataset(_funding_and_disclosure())
    cover = coverage(ds, [], [])
    changed = {"fund-20", "fund-21", "fund-22"}
    subjects = candidates(ds, cover, changed, run_date=RUN, limits=SMALL)
    funding = next(s for s in subjects if s.key == "provision:appropriates-funding")
    assert "broad provision type" not in funding.why
    assert funding.why["changed this week"] == 24
    quiet = candidates(ds, cover, set(), run_date=RUN, limits=SMALL)
    assert funding.score > 5 * next(s.score for s in quiet if s.key == funding.key)


def test_a_subject_keeps_its_most_significant_records():
    ds = _dataset(_funding_and_disclosure())
    limits = Limits(**{**SMALL.__dict__, "max_subject_records": 12})
    subjects = candidates(ds, coverage(ds, [], []), {"fund-29"}, run_date=RUN, limits=limits)
    funding = next(s for s in subjects if s.key == "provision:appropriates-funding")
    assert len(funding.records) == 12 and len(funding.group) == 30
    assert funding.records[0] == "fund-29"  # changed this week first
    assert set(funding.records[1:11]) == {f"fund-{i}" for i in range(10)}  # then enacted
    assert "12 most significant of 30" in funding.label
    ctx = build_context(ds, funding, set(), [], run_date=RUN, max_records=60)
    facts = ctx.payload["facts"]
    assert facts["records_in_subject"] == 12 and facts["whole_group"]["records"] == 30
    assert "only the 12 records" in facts["selection"]


def test_a_recent_federal_action_is_a_subject_until_an_article_cites_it():
    rule = _rec(
        "us-ed-rule", "us", "current", "procurement-standards", day="2026-10-10", kind="document"
    )
    related = [
        _rec(f"us-{i}", "us", "in-committee", "procurement-standards", day="2026-06-01")
        for i in range(2)
    ]
    ds = _dataset([rule, *related])
    subjects = candidates(ds, coverage(ds, [], []), set(), run_date=RUN, limits=SMALL)
    fed = next(s for s in subjects if s.key == "federal:us-ed-rule")
    assert fed.type == "Explainer" and fed.records[0] == "us-ed-rule" and fed.why["federal action"]
    citing = Article("x", Path("x"), "", "", "Explainer", "2026-10-12", [("us-ed-rule", 0)], [])
    cover = coverage(ds, [citing], [])
    assert not any(
        s.key.startswith("federal:")
        for s in candidates(ds, cover, set(), run_date=RUN, limits=SMALL)
    )


def test_rotation_adds_to_the_score_and_shows_in_why():
    ds = _dataset(_funding_and_disclosure())
    cover = coverage(ds, [], [])
    pick = choose(candidates(ds, cover, set(), run_date=RUN, limits=SMALL), cover)
    assert pick.type == "Explainer" and pick.why["type rotation"] == 40
    assert pick.score == pytest.approx(sum(pick.why.values()), abs=0.05)


def test_scoring_settings_load_and_reject_unknown_keys():
    from edictai.articles.subjects import Scoring

    sc = Scoring.from_mapping({"changed": 2, "broad": ["creates-task-force"], "count_cap": "4"})
    assert sc.changed == 2.0 and sc.broad == ("creates-task-force",) and sc.count_cap == 4
    assert Scoring.from_mapping(None) == Scoring()
    with pytest.raises(ValueError, match="chnaged"):
        Scoring.from_mapping({"chnaged": 1})


# -- opening, closing and length checks ---------------------------------------------------


def test_statistics_skip_years_dates_identifiers_and_grade_spans():
    from edictai.articles.check import statistics_in

    text = (
        "AB 1159, signed on September 10, 2026, covers K-12 and the 2024-25 school year; "
        "H.R. 10719 and Executive Order 14110 cover 14 states and $500,000 [3]."
    )
    assert statistics_in(text) == ["14", "500,000"]


def test_a_count_heavy_opening_is_rejected(repo):
    heavy = (
        "As of October 2026, Edictai's records show 140 records with such a provision, 126 "
        "of them legislation and 14 documents, across 28 jurisdictions [1]."
    )
    errs = errors_for(repo, reply(paragraph=heavy))
    assert any("first paragraph has 4 statistics" in e for e in errs)
    assert not any("first paragraph" in e for e in errors_for(repo, reply()))


def test_a_closing_tally_is_rejected(repo):
    r = reply()
    r["body"].append(
        {
            "p": "Of the 126 legislative records, 50 remain in committee, 39 failed, five "
            "passed one chamber, three passed the legislature and two were vetoed."
        }
    )
    assert any("last paragraph is mostly counts" in e for e in errors_for(repo, r))


def test_advisories_flag_length_near_the_ceiling_and_a_self_describing_opening():
    from edictai.articles.check import advisories

    long = {"body": [{"p": "This explainer covers " + "word " * 1100}]}
    notes = advisories(long, max_words=1200, target_words=850)
    assert any("near the ceiling" in n for n in notes)
    assert any("describes the article" in n for n in notes)
    assert (
        advisories(
            {"body": [{"p": "HB 1 requires a policy. " * 100}]}, max_words=1200, target_words=850
        )
        == []
    )
