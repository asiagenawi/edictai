-- StateScope database schema.
--
-- Built from the YAML records in data/ by `statescope build`; the database is a
-- derived artifact, so corrections belong in the YAML, not here. Column names
-- follow the YAML field names, so docs/data/dictionary.md describes both.
-- Lists in the YAML become child tables keyed by the parent id and a 0-based
-- `seq` that preserves the order the record gives them.
--
-- Enumerated columns carry CHECK constraints listing the codes in
-- data/vocab/enums.yaml; the pipeline test suite fails if the two drift apart.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Controlled vocabularies
-- ---------------------------------------------------------------------------

-- Subject topics, from data/vocab/topics.yaml.
CREATE TABLE topics (
    code TEXT PRIMARY KEY, -- Stable topic code, e.g. 'ai-literacy'.
    name TEXT NOT NULL, -- Display name.
    description TEXT, -- What the topic covers.
    position INTEGER NOT NULL UNIQUE, -- 1-based order in topics.yaml. Codes are only appended, so this is also a stable integer key.
    k12 INTEGER NOT NULL CHECK (k12 IN (0, 1)), -- 1 if the topic applies to K-12.
    postsecondary INTEGER NOT NULL CHECK (postsecondary IN (0, 1)) -- 1 if the topic applies to higher education.
);

-- Provision types: the columns of the cross-state comparison grid, from data/vocab/provision-types.yaml.
CREATE TABLE provision_types (
    code TEXT PRIMARY KEY, -- Stable provision type code, e.g. 'requires-local-ai-policy'.
    name TEXT NOT NULL, -- Display name.
    topic TEXT NOT NULL REFERENCES topics (code), -- Topic the provision type falls under.
    position INTEGER NOT NULL UNIQUE, -- 1-based order in provision-types.yaml.
    k12 INTEGER NOT NULL CHECK (k12 IN (0, 1)), -- 1 if the provision type applies to K-12.
    postsecondary INTEGER NOT NULL CHECK (postsecondary IN (0, 1)) -- 1 if the provision type applies to higher education.
);

-- ---------------------------------------------------------------------------
-- Record registry
-- ---------------------------------------------------------------------------

-- Every record in data/, so tables that can point at any record type have a real foreign key.
CREATE TABLE records (
    id TEXT PRIMARY KEY, -- Record id; matches the file path.
    record_type TEXT NOT NULL CHECK (record_type IN ('jurisdiction', 'organization', 'legislation', 'document', 'executive-action', 'research')), -- Kind of record.
    path TEXT NOT NULL UNIQUE, -- Source file, relative to the repository root.
    num_id INTEGER NOT NULL UNIQUE, -- Integer alias derived from a hash of id: stable across builds, for clients that need numeric keys.
    UNIQUE (id, record_type)
);

-- ---------------------------------------------------------------------------
-- Reference records
-- ---------------------------------------------------------------------------

-- The United States, the 50 states, and DC.
CREATE TABLE jurisdictions (
    id TEXT PRIMARY KEY REFERENCES records (id), -- 'us' for federal, 'us-xx' for a state or DC.
    ocd_id TEXT NOT NULL UNIQUE, -- Open Civic Data division identifier.
    name TEXT NOT NULL, -- Full name.
    abbreviation TEXT NOT NULL UNIQUE, -- USPS code, or 'US'.
    classification TEXT NOT NULL CHECK (classification IN ('country', 'state', 'district')), -- Kind of jurisdiction.
    fips TEXT, -- Two-digit FIPS state code.
    region TEXT CHECK (region IN ('Northeast', 'Southeast', 'Midwest', 'Southwest', 'West')), -- Census-style region.
    legislature_url TEXT -- Legislature or Congress website.
);

-- Legislative sessions, from the sessions list in each jurisdiction file.
CREATE TABLE sessions (
    jurisdiction TEXT NOT NULL REFERENCES jurisdictions (id), -- Jurisdiction the session belongs to.
    identifier TEXT NOT NULL, -- Session identifier used in legislation ids, e.g. '2025', '119'.
    name TEXT NOT NULL, -- Display name.
    classification TEXT CHECK (classification IN ('primary', 'special')), -- Regular or special session.
    start_date TEXT NOT NULL, -- First day of the session.
    end_date TEXT, -- Last day of the session; bills short of final passage after this date are failed.
    legiscan_session_id INTEGER, -- LegiScan session id.
    PRIMARY KEY (jurisdiction, identifier)
);

-- Sources cited for each session's dates.
CREATE TABLE session_sources (
    jurisdiction TEXT NOT NULL, -- Jurisdiction the session belongs to.
    session TEXT NOT NULL, -- Session identifier.
    seq INTEGER NOT NULL, -- Position in the session's sources list.
    url TEXT NOT NULL, -- Source URL.
    title TEXT, -- Source title.
    publisher TEXT, -- Publisher.
    kind TEXT NOT NULL CHECK (kind IN ('official', 'secondary')), -- Official text or third-party reporting.
    accessed TEXT NOT NULL, -- Date last retrieved.
    archived_url TEXT, -- Archive copy.
    PRIMARY KEY (jurisdiction, session, seq),
    FOREIGN KEY (jurisdiction, session) REFERENCES sessions (jurisdiction, identifier)
);

-- Bodies that issue policy.
CREATE TABLE organizations (
    id TEXT PRIMARY KEY REFERENCES records (id), -- Organization id, e.g. 'us-ca-k12-agency', 'us-ed', 'hlc'.
    name TEXT NOT NULL, -- Full name.
    abbreviation TEXT, -- Common abbreviation.
    classification TEXT NOT NULL CHECK (classification IN ('legislature', 'governor', 'k12-agency', 'higher-ed-agency', 'university-system', 'accreditor', 'federal-agency', 'commission', 'other')), -- Kind of organization.
    jurisdiction TEXT REFERENCES jurisdictions (id), -- Home jurisdiction; NULL for bodies that span states.
    url TEXT NOT NULL -- Website.
);

-- Pages the change watcher monitors for each organization.
CREATE TABLE organization_watch_urls (
    organization_id TEXT NOT NULL REFERENCES organizations (id), -- Organization.
    seq INTEGER NOT NULL, -- Position in the record's list.
    url TEXT NOT NULL, -- Monitored page.
    PRIMARY KEY (organization_id, seq)
);

-- ---------------------------------------------------------------------------
-- Legislation
-- ---------------------------------------------------------------------------

-- Bills and resolutions. `status` and the dates beside it are derived from legislation_actions.
CREATE TABLE legislation (
    id TEXT PRIMARY KEY REFERENCES records (id), -- {jurisdiction}-{session}-{bill-slug}, e.g. 'us-tx-2025-hb-1481'.
    jurisdiction TEXT NOT NULL REFERENCES jurisdictions (id), -- Jurisdiction.
    session TEXT NOT NULL, -- Session identifier.
    identifier TEXT NOT NULL, -- Official bill number, e.g. 'HB 1481'.
    title TEXT NOT NULL, -- Official or short title.
    classification TEXT NOT NULL CHECK (classification IN ('bill', 'resolution', 'joint-resolution', 'concurrent-resolution', 'constitutional-amendment', 'appropriation')), -- Kind of measure.
    summary TEXT NOT NULL, -- Plain-language summary.
    abstract TEXT, -- Official digest, verbatim.
    session_law TEXT, -- Chapter or public act number once enacted.
    status TEXT NOT NULL CHECK (status IN ('introduced', 'in-committee', 'passed-chamber', 'passed-legislature', 'enacted', 'vetoed', 'failed')), -- Derived from the actions at build time.
    status_date TEXT, -- Date of the action that set the status; NULL when the session ending did.
    introduced_date TEXT, -- Date of the first action.
    enacted_date TEXT, -- Date the bill was signed or became law.
    last_action_date TEXT, -- Date of the most recent action.
    verification_status TEXT NOT NULL CHECK (verification_status IN ('editor-reviewed', 'machine-verified', 'unverified')), -- How the record was checked.
    last_verified TEXT, -- Date of the last check against the source.
    verification_method TEXT, -- How the check was done.
    verification_notes TEXT, -- Notes from the check.
    FOREIGN KEY (jurisdiction, session) REFERENCES sessions (jurisdiction, identifier)
);
CREATE INDEX idx_legislation_jurisdiction ON legislation (jurisdiction, session);
CREATE INDEX idx_legislation_status ON legislation (status);

-- Dated legislative history, in the order the record lists it.
CREATE TABLE legislation_actions (
    legislation_id TEXT NOT NULL REFERENCES legislation (id), -- Bill.
    seq INTEGER NOT NULL, -- Position in the record's actions list.
    date TEXT NOT NULL, -- Date of the action.
    actor TEXT CHECK (actor IN ('lower', 'upper', 'legislature', 'executive', 'committee', 'other')), -- Who acted.
    description TEXT NOT NULL, -- Action text as recorded by the legislature.
    PRIMARY KEY (legislation_id, seq)
);
CREATE INDEX idx_legislation_actions_date ON legislation_actions (date);

-- Classifications of each action; an action can carry several.
CREATE TABLE legislation_action_classifications (
    legislation_id TEXT NOT NULL, -- Bill.
    seq INTEGER NOT NULL, -- Action position, as in legislation_actions.
    classification TEXT NOT NULL CHECK (classification IN ('introduction', 'reading-1', 'reading-2', 'reading-3', 'referral-committee', 'committee-hearing', 'committee-passage', 'committee-failure', 'deferral', 'amendment-passage', 'passage', 'failure', 'withdrawal', 'substitution', 'conference-committee', 'enrollment', 'executive-receipt', 'executive-signature', 'executive-veto', 'veto-override-passage', 'became-law', 'died-at-session-end', 'other')), -- Action classification.
    PRIMARY KEY (legislation_id, seq, classification),
    FOREIGN KEY (legislation_id, seq) REFERENCES legislation_actions (legislation_id, seq)
);
CREATE INDEX idx_action_classifications ON legislation_action_classifications (classification);

-- Published texts of a bill.
CREATE TABLE legislation_versions (
    legislation_id TEXT NOT NULL REFERENCES legislation (id), -- Bill.
    seq INTEGER NOT NULL, -- Position in the record's versions list.
    note TEXT NOT NULL, -- e.g. 'Introduced', 'Enrolled'.
    date TEXT, -- Date of the version.
    url TEXT NOT NULL, -- Text of the version.
    archived_url TEXT, -- Archive copy.
    media_type TEXT, -- MIME type of the text.
    PRIMARY KEY (legislation_id, seq)
);

-- Sponsors of a bill.
CREATE TABLE legislation_sponsors (
    legislation_id TEXT NOT NULL REFERENCES legislation (id), -- Bill.
    seq INTEGER NOT NULL, -- Position in the record's sponsors list.
    name TEXT NOT NULL, -- Sponsor name.
    classification TEXT NOT NULL CHECK (classification IN ('person', 'committee')), -- A legislator or a committee; 'person' when the record leaves it out.
    primary_sponsor INTEGER NOT NULL CHECK (primary_sponsor IN (0, 1)), -- The YAML field `primary`: 1 for a primary sponsor, 0 for a cosponsor.
    party TEXT, -- Party.
    chamber TEXT CHECK (chamber IN ('lower', 'upper')), -- Chamber.
    openstates_person_id TEXT, -- Open States person id.
    PRIMARY KEY (legislation_id, seq)
);

-- ---------------------------------------------------------------------------
-- Documents and executive actions
-- ---------------------------------------------------------------------------

-- Non-legislative policy: guidance, frameworks, binding policy, toolkits, statements, reports.
CREATE TABLE documents (
    id TEXT PRIMARY KEY REFERENCES records (id), -- {jurisdiction}-{slug}.
    jurisdiction TEXT NOT NULL REFERENCES jurisdictions (id), -- Jurisdiction.
    issuer TEXT REFERENCES organizations (id), -- Issuing organization; NULL until its organization file exists.
    title TEXT NOT NULL, -- Title.
    classification TEXT NOT NULL CHECK (classification IN ('guidance', 'policy', 'framework', 'toolkit', 'statement', 'report', 'model-policy', 'program')), -- Kind of document.
    status TEXT NOT NULL CHECK (status IN ('current', 'superseded', 'withdrawn')), -- Whether the document is in force.
    summary TEXT NOT NULL, -- Plain-language summary.
    published TEXT, -- Date first issued, when known: YYYY, YYYY-MM, or YYYY-MM-DD.
    updated TEXT, -- Date of the current edition or last update shown by the issuer: YYYY, YYYY-MM, or YYYY-MM-DD.
    version TEXT, -- Edition or version number of the current text.
    effective TEXT, -- Date binding policy takes effect.
    undated INTEGER NOT NULL CHECK (undated IN (0, 1)), -- 1 when neither the document nor its issuer gives a date.
    superseded_by TEXT REFERENCES documents (id), -- Document that replaced this one.
    authorized_by TEXT REFERENCES records (id), -- Legislation or executive action that required this document.
    verification_status TEXT NOT NULL CHECK (verification_status IN ('editor-reviewed', 'machine-verified', 'unverified')), -- How the record was checked.
    last_verified TEXT, -- Date of the last check against the source.
    verification_method TEXT, -- How the check was done.
    verification_notes TEXT -- Notes from the check.
);
CREATE INDEX idx_documents_jurisdiction ON documents (jurisdiction);
CREATE INDEX idx_documents_issuer ON documents (issuer);

-- Observed versions of a document, oldest first.
CREATE TABLE document_revisions (
    document_id TEXT NOT NULL REFERENCES documents (id), -- Document.
    seq INTEGER NOT NULL, -- Position in the record's revisions list.
    observed TEXT NOT NULL, -- Date the version was seen.
    url TEXT NOT NULL, -- Where it was seen.
    archived_url TEXT, -- Archive copy.
    sha256 TEXT CHECK (sha256 IS NULL OR length(sha256) = 64), -- Hash of the normalized extracted text.
    note TEXT, -- What changed.
    PRIMARY KEY (document_id, seq)
);

-- Executive orders and directives from governors and the President.
CREATE TABLE executive_actions (
    id TEXT PRIMARY KEY REFERENCES records (id), -- {jurisdiction}-{slug}.
    jurisdiction TEXT NOT NULL REFERENCES jurisdictions (id), -- Jurisdiction.
    issuer TEXT REFERENCES organizations (id), -- Issuing office; NULL until its organization file exists.
    number TEXT, -- Official order number.
    title TEXT NOT NULL, -- Title.
    classification TEXT NOT NULL CHECK (classification IN ('executive-order', 'directive', 'proclamation', 'memorandum')), -- Kind of action.
    status TEXT NOT NULL CHECK (status IN ('active', 'rescinded', 'expired', 'superseded')), -- Whether the action is in force.
    signed TEXT NOT NULL, -- Date signed.
    ended TEXT, -- Date rescinded or expired.
    expires TEXT, -- Scheduled expiration date stated in the order.
    summary TEXT NOT NULL, -- Plain-language summary.
    verification_status TEXT NOT NULL CHECK (verification_status IN ('editor-reviewed', 'machine-verified', 'unverified')), -- How the record was checked.
    last_verified TEXT, -- Date of the last check against the source.
    verification_method TEXT, -- How the check was done.
    verification_notes TEXT -- Notes from the check.
);
CREATE INDEX idx_executive_actions_jurisdiction ON executive_actions (jurisdiction);

-- ---------------------------------------------------------------------------
-- Research
-- ---------------------------------------------------------------------------

-- Studies, reports, briefs, and surveys on AI in education.
CREATE TABLE research (
    id TEXT PRIMARY KEY REFERENCES records (id), -- {year}-{slug}.
    title TEXT NOT NULL, -- Title.
    publisher TEXT NOT NULL, -- Journal or publishing organization.
    published TEXT NOT NULL, -- Publication date: YYYY, YYYY-MM, or YYYY-MM-DD.
    classification TEXT NOT NULL CHECK (classification IN ('peer-reviewed', 'report', 'brief', 'survey', 'dataset', 'meta-analysis')), -- Kind of research.
    summary TEXT NOT NULL, -- What was studied, how, and what was found.
    methods TEXT, -- Design and sample.
    verification_status TEXT NOT NULL CHECK (verification_status IN ('editor-reviewed', 'machine-verified', 'unverified')), -- How the record was checked.
    last_verified TEXT, -- Date of the last check against the source.
    verification_method TEXT, -- How the check was done.
    verification_notes TEXT -- Notes from the check.
);

-- Authors of a research item, in byline order.
CREATE TABLE research_authors (
    research_id TEXT NOT NULL REFERENCES research (id), -- Research item.
    seq INTEGER NOT NULL, -- Byline position.
    name TEXT NOT NULL, -- Author.
    PRIMARY KEY (research_id, seq)
);

-- Jurisdictions a study covers.
CREATE TABLE research_geography (
    research_id TEXT NOT NULL REFERENCES research (id), -- Research item.
    jurisdiction TEXT NOT NULL REFERENCES jurisdictions (id), -- Jurisdiction covered.
    PRIMARY KEY (research_id, jurisdiction)
);

-- Cited findings of a research item.
CREATE TABLE research_findings (
    research_id TEXT NOT NULL REFERENCES research (id), -- Research item.
    seq INTEGER NOT NULL, -- Position in the record's key_findings list.
    finding TEXT NOT NULL, -- The finding, in plain language.
    source_url TEXT NOT NULL, -- Source supporting it.
    locator TEXT, -- Section, page, or paragraph.
    quote TEXT, -- Short verbatim excerpt.
    PRIMARY KEY (research_id, seq)
);

-- ---------------------------------------------------------------------------
-- Tables shared by several record types
-- ---------------------------------------------------------------------------

-- Cited provisions: what a law, document, or executive action actually does, clause by clause.
CREATE TABLE provisions (
    id INTEGER PRIMARY KEY, -- Row id, assigned in build order; use (record_id, seq) as a stable key.
    record_id TEXT NOT NULL, -- Record containing the provision.
    record_type TEXT NOT NULL, -- Its record type.
    seq INTEGER NOT NULL, -- Position in the record's provisions list.
    type TEXT NOT NULL REFERENCES provision_types (code), -- Provision type.
    description TEXT NOT NULL, -- Plain-language statement of what the provision does.
    source_url TEXT NOT NULL, -- Source supporting it.
    locator TEXT, -- Section, page, or paragraph.
    quote TEXT, -- Short verbatim excerpt.
    UNIQUE (record_id, seq),
    FOREIGN KEY (record_id, record_type) REFERENCES records (id, record_type)
);
CREATE INDEX idx_provisions_type ON provisions (type);

-- Education levels each provision applies to: its own list, or the record's when it has none.
CREATE TABLE provision_education_levels (
    provision_id INTEGER NOT NULL REFERENCES provisions (id), -- Provision.
    education_level TEXT NOT NULL CHECK (education_level IN ('k12', 'postsecondary')), -- Education level.
    inherited INTEGER NOT NULL CHECK (inherited IN (0, 1)), -- 1 when taken from the record because the provision lists none.
    PRIMARY KEY (provision_id, education_level)
);

-- Topics assigned to each record.
CREATE TABLE record_topics (
    record_id TEXT NOT NULL, -- Record.
    record_type TEXT NOT NULL, -- Its record type.
    topic TEXT NOT NULL REFERENCES topics (code), -- Topic.
    PRIMARY KEY (record_id, topic),
    FOREIGN KEY (record_id, record_type) REFERENCES records (id, record_type)
);
CREATE INDEX idx_record_topics_topic ON record_topics (topic);

-- Education levels assigned to each record.
CREATE TABLE record_education_levels (
    record_id TEXT NOT NULL, -- Record.
    record_type TEXT NOT NULL, -- Its record type.
    education_level TEXT NOT NULL CHECK (education_level IN ('k12', 'postsecondary')), -- Education level.
    PRIMARY KEY (record_id, education_level),
    FOREIGN KEY (record_id, record_type) REFERENCES records (id, record_type)
);
CREATE INDEX idx_record_education_levels_level ON record_education_levels (education_level);

-- Sources cited by each record.
CREATE TABLE sources (
    record_id TEXT NOT NULL, -- Record citing the source.
    record_type TEXT NOT NULL, -- Its record type.
    seq INTEGER NOT NULL, -- Position in the record's sources list.
    url TEXT NOT NULL, -- Source URL.
    title TEXT, -- Source title.
    publisher TEXT, -- Publisher.
    kind TEXT NOT NULL CHECK (kind IN ('official', 'secondary')), -- Official text or third-party reporting.
    accessed TEXT NOT NULL, -- Date last retrieved.
    archived_url TEXT, -- Archive copy.
    PRIMARY KEY (record_id, seq),
    FOREIGN KEY (record_id, record_type) REFERENCES records (id, record_type)
);

-- Links between records: companion bills, guidance a law produced, and so on.
CREATE TABLE related_records (
    record_id TEXT NOT NULL REFERENCES records (id), -- Record listing the link.
    related_id TEXT NOT NULL REFERENCES records (id), -- Record it links to.
    PRIMARY KEY (record_id, related_id)
);
CREATE INDEX idx_related_records_related ON related_records (related_id);

-- Identifiers for records in upstream systems.
CREATE TABLE external_ids (
    record_id TEXT NOT NULL, -- Record.
    record_type TEXT NOT NULL, -- Its record type.
    scheme TEXT NOT NULL CHECK (scheme IN ('legiscan_bill_id', 'openstates_id', 'congress_gov', 'federal_register_document_number', 'doi', 'openalex_id')), -- Upstream system.
    value TEXT NOT NULL, -- Identifier in that system.
    PRIMARY KEY (record_id, scheme),
    FOREIGN KEY (record_id, record_type) REFERENCES records (id, record_type)
);

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

-- Legislation, documents, and executive actions in one shape. `is_current` is 1 for enacted legislation, current documents, and active executive actions. `date` is the first action for legislation, the current edition for documents (updated, else published; NULL when undated), and the signing date for executive actions.
CREATE VIEW v_policies AS
SELECT
    r.num_id,
    p.id,
    p.record_type,
    p.jurisdiction,
    j.name AS jurisdiction_name,
    CASE WHEN j.classification = 'country' THEN NULL ELSE j.abbreviation END AS state_code,
    CASE WHEN j.classification = 'country' THEN 'federal' ELSE 'state' END AS level,
    p.issuer,
    o.name AS issuer_name,
    p.identifier,
    p.title,
    p.classification,
    p.status,
    p.is_current,
    p.date,
    p.enacted_date,
    p.summary,
    p.abstract,
    p.sponsors,
    (
        SELECT s.url FROM sources s
        WHERE s.record_id = p.id
        ORDER BY s.kind <> 'official', s.seq
        LIMIT 1
    ) AS source_url,
    p.verification_status,
    p.last_verified
FROM (
    SELECT
        l.id, 'legislation' AS record_type, l.jurisdiction, NULL AS issuer, l.identifier,
        l.title, l.classification, l.status, l.status = 'enacted' AS is_current,
        l.introduced_date AS date, l.enacted_date, l.summary, l.abstract,
        (
            SELECT group_concat(sp.name, '; ') FROM (
                SELECT name FROM legislation_sponsors
                WHERE legislation_id = l.id AND primary_sponsor = 1 ORDER BY seq
            ) sp
        ) AS sponsors,
        l.verification_status, l.last_verified
    FROM legislation l
    UNION ALL
    SELECT
        d.id, 'document', d.jurisdiction, d.issuer, NULL,
        d.title, d.classification, d.status, d.status = 'current',
        COALESCE(d.updated, d.published), NULL, d.summary, NULL, NULL,
        d.verification_status, d.last_verified
    FROM documents d
    UNION ALL
    SELECT
        e.id, 'executive-action', e.jurisdiction, e.issuer, e.number,
        e.title, e.classification, e.status, e.status = 'active',
        e.signed, e.signed, e.summary, NULL, NULL,
        e.verification_status, e.last_verified
    FROM executive_actions e
) p
JOIN records r ON r.id = p.id
JOIN jurisdictions j ON j.id = p.jurisdiction
LEFT JOIN organizations o ON o.id = p.issuer;

-- One row per jurisdiction and provision type, including pairs with no provisions, for building the comparison grid.
CREATE VIEW v_provision_grid AS
SELECT
    j.id AS jurisdiction,
    j.abbreviation,
    pt.code AS provision_type,
    pt.name AS provision_type_name,
    COUNT(DISTINCT x.record_id) AS records,
    COUNT(DISTINCT CASE WHEN x.is_current THEN x.record_id END) AS current_records,
    group_concat(DISTINCT x.record_id) AS record_ids
FROM jurisdictions j
CROSS JOIN provision_types pt
LEFT JOIN (
    SELECT pr.record_id, pr.type, vp.jurisdiction, vp.is_current
    FROM provisions pr
    JOIN v_policies vp ON vp.id = pr.record_id
) x ON x.jurisdiction = j.id AND x.type = pt.code
GROUP BY j.id, pt.code;
