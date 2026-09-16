"""state/schema: The SQL schema and its migrations, as data.  Moved from bin/spud_ledger.py (SPD-065)."""

# ----------------------------------------------------------------------------
# Database: schema, migrations, connections
# ----------------------------------------------------------------------------

DDL_0001 = """
CREATE TABLE projects (
  id            INTEGER PRIMARY KEY,
  key           TEXT    NOT NULL UNIQUE,          -- 'spud', 'badtakes'
  name          TEXT    NOT NULL,
  root_path     TEXT    NOT NULL UNIQUE,          -- absolute path of the main checkout
  remote        TEXT,                             -- git remote URL, informational
  ticket_prefix TEXT    NOT NULL UNIQUE,          -- 'SPD'  (per-project prefixes, Eric 2026-09-12)
  team_prefix   TEXT    NOT NULL UNIQUE,          -- 'SPUD'
  created_at    TEXT    NOT NULL
) STRICT;

CREATE TABLE tickets (
  id          INTEGER PRIMARY KEY,                -- surrogate; the number lives in `number`
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  number      INTEGER NOT NULL,                   -- per-project counter: max(number) + 1 in the insert transaction
  key         TEXT    NOT NULL UNIQUE,            -- ticket_prefix || '-' || printf('%03d', number)
  team_key    TEXT    NOT NULL UNIQUE,            -- team_prefix   || '-' || printf('%03d', number)
  title       TEXT    NOT NULL,
  heading     TEXT,                               -- the H1 tail when the file shortens it against title
  priority    TEXT    NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
  status      TEXT    NOT NULL CHECK (status IN ('queued','active','done','declined')),
  origin      TEXT    NOT NULL CHECK (origin IN ('eric','proposal')),
  proposal_id INTEGER REFERENCES proposals(id),   -- set when origin = 'proposal'; renders as proposed_by
  lead_id     INTEGER REFERENCES members(id),     -- the first member; NULL until one is planned
  brief       TEXT    NOT NULL DEFAULT '',
  sizing      TEXT    NOT NULL DEFAULT '',        -- "Size, persona and model decision"
  outcome     TEXT    NOT NULL DEFAULT '',
  tags        TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
  layout      TEXT    CHECK (layout IS NULL OR json_valid(layout)),  -- imported file's key and section order; NULL = template
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL,
  closed_at   TEXT,
  UNIQUE (project_id, number)
) STRICT;
CREATE INDEX tickets_board ON tickets(status, priority);

CREATE TABLE members (                            -- one row per spudagent or contractor; the team tree
  id            INTEGER PRIMARY KEY,
  ticket_id     INTEGER NOT NULL REFERENCES tickets(id),
  lineage       TEXT    NOT NULL,                 -- '01', '01.02', '01.01.01' (materialized path)
  depth         INTEGER NOT NULL,                 -- segments in lineage; Spud is depth 0 and has no row
  parent_id     INTEGER REFERENCES members(id),   -- NULL = Spud
  name          TEXT    NOT NULL,                 -- from the pool; unique within the ticket only
  persona       TEXT    NOT NULL CHECK (persona IN ('researcher','architect','reviewer',
                                                    'engineer','designer','writer','scout','contractor')),
  agent_type    TEXT    NOT NULL DEFAULT 'spudagent',
  model         TEXT    NOT NULL CHECK (model IN ('fable','opus','sonnet','haiku')),
  tier_reason   TEXT,
  status        TEXT    NOT NULL CHECK (status IN ('planned','active','done','blocked','failed')),
  brief         TEXT    NOT NULL DEFAULT '',      -- parent-owned
  deliverables  TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(deliverables)),
  result        TEXT,                             -- member-owned  (## Result)
  blocked       TEXT,                             -- member-owned  (## Blocked)
  outcome       TEXT,                             -- parent-owned  (## Outcome)
  summary       TEXT,                             -- parent-owned, one paragraph for the card
  layout        TEXT    CHECK (layout IS NULL OR json_valid(layout)),  -- imported file's section order; NULL = template
  planned_at    TEXT    NOT NULL,
  spawned_at    TEXT,
  stopped_at    TEXT,
  finished_at   TEXT,
  agent_id      TEXT    UNIQUE,                   -- native Claude Code id, bound by hooks (SPD-008)
  session_id    TEXT,
  tool_use_id   TEXT,
  resolved_model TEXT,
  transcript_path TEXT,
  return_text   TEXT,
  total_tokens  INTEGER,
  duration_ms   INTEGER,
  tool_uses     INTEGER,
  usage_json    TEXT    CHECK (usage_json IS NULL OR json_valid(usage_json)),
  UNIQUE (ticket_id, name),
  UNIQUE (ticket_id, lineage),
  CHECK  (depth = length(lineage) - length(replace(lineage, '.', '')) + 1)
) STRICT;
CREATE INDEX members_tree ON members(ticket_id, parent_id);
CREATE INDEX members_live ON members(status) WHERE status IN ('planned','active');

CREATE TABLE spawn_requests (                     -- one row per PreToolUse(Agent); written by hooks (SPD-008)
  tool_use_id       TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL,
  at                TEXT NOT NULL,
  caller_agent_id   TEXT,
  description       TEXT NOT NULL,
  subagent_type     TEXT,
  model             TEXT,
  run_in_background INTEGER,
  member_id         INTEGER REFERENCES members(id),
  agent_id          TEXT,
  decision          TEXT NOT NULL CHECK (decision IN ('allow','deny')),
  reason            TEXT
) STRICT;

CREATE TABLE events (                             -- append-only; the memory that outlives every session
  id        INTEGER PRIMARY KEY,
  at        TEXT    NOT NULL,
  actor     TEXT    NOT NULL,                     -- 'spud' | 'member:<id>' | 'agent:<agent_id>' | 'hook:<event>' | 'eric' | 'import'
  ticket_id INTEGER REFERENCES tickets(id),
  member_id INTEGER REFERENCES members(id),
  agent_id  TEXT,
  kind      TEXT    NOT NULL CHECK (kind IN (
              'ticket.created','ticket.status','ticket.priority','ticket.edited',
              'member.planned','member.spawn_denied','member.spawned','member.started','member.stopped',
              'member.log','member.result','member.blocked','member.outcome','member.status','member.edited',
              'handoff','proposal.filed','proposal.decided','hook.denied','hook.error','render',
              'report.entry','commit','import','config.synced')),
  body      TEXT    NOT NULL DEFAULT '',
  data      TEXT    CHECK (data IS NULL OR json_valid(data))
) STRICT;
CREATE INDEX events_ticket ON events(ticket_id, id);
CREATE INDEX events_member ON events(member_id, id);
CREATE INDEX events_agent  ON events(agent_id, id);
CREATE INDEX events_kind   ON events(kind, id);

CREATE TABLE handoffs (
  id             INTEGER PRIMARY KEY,
  ticket_id      INTEGER NOT NULL REFERENCES tickets(id),
  at             TEXT    NOT NULL,
  from_member_id INTEGER REFERENCES members(id),  -- NULL = Spud
  to_member_id   INTEGER REFERENCES members(id),  -- NULL = Spud
  what           TEXT    NOT NULL,
  path           TEXT
) STRICT;

CREATE TABLE proposals (
  id                 INTEGER PRIMARY KEY,
  ticket_id          INTEGER NOT NULL REFERENCES tickets(id),   -- the ticket it arose on
  origin_member_id   INTEGER NOT NULL REFERENCES members(id),   -- who first wrote it
  holder_member_id   INTEGER REFERENCES members(id),            -- who must decide next; NULL = Spud
  title              TEXT    NOT NULL,
  why                TEXT    NOT NULL DEFAULT '',
  evidence           TEXT    NOT NULL DEFAULT '',
  suggested_priority TEXT    CHECK (suggested_priority IN ('P0','P1','P2','P3')),
  filed_at           TEXT    NOT NULL,
  status             TEXT    NOT NULL CHECK (status IN ('open','absorbed','declined','created')),
  created_ticket_id  INTEGER REFERENCES tickets(id)
) STRICT;

CREATE TABLE proposal_decisions (                 -- the climb, one row per parent that touched it
  id                INTEGER PRIMARY KEY,
  proposal_id       INTEGER NOT NULL REFERENCES proposals(id),
  at                TEXT    NOT NULL,
  by_member_id      INTEGER REFERENCES members(id),   -- NULL = Spud
  decision          TEXT    NOT NULL CHECK (decision IN ('absorb','decline','escalate','create')),
  reason            TEXT    NOT NULL DEFAULT '',
  created_ticket_id INTEGER REFERENCES tickets(id)
) STRICT;

CREATE TABLE name_pool (                          -- mirror of naming.pool; `spud config sync` refreshes it
  name   TEXT    PRIMARY KEY,
  active INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE TABLE renders (                            -- what the renderer last wrote, to detect hand edits
  path             TEXT PRIMARY KEY,              -- relative to Spud's home, e.g. 'ledger/tickets/SPD-006.md'
  sha256           TEXT NOT NULL,
  content          TEXT NOT NULL DEFAULT '',      -- the text written, so `import --file` can tell a hand edit from a CLI change made since
  rendered_at      TEXT NOT NULL,
  through_event_id INTEGER NOT NULL
) STRICT;

CREATE TABLE imported_sections (                  -- markdown-v0 prose the rows cannot regenerate, kept verbatim
  entity     TEXT    NOT NULL CHECK (entity IN ('ticket','member')),
  entity_id  INTEGER NOT NULL,
  section    TEXT    NOT NULL,                    -- 'Team', 'Log', 'Sources', ...
  body       TEXT    NOT NULL,
  through_id INTEGER NOT NULL DEFAULT 0,          -- rows of the section's table with id > through_id render after the prose
  PRIMARY KEY (entity, entity_id, section)
) STRICT;
"""

VIEWS_AND_TRIGGERS = """
DROP VIEW IF EXISTS v_board;
DROP VIEW IF EXISTS v_fleet;
DROP VIEW IF EXISTS v_live;
DROP TRIGGER IF EXISTS events_no_update;
DROP TRIGGER IF EXISTS events_no_delete;

CREATE VIEW v_board AS                            -- what Board.base shows, for the CLI and for rendering
SELECT t.key, (SELECT pr.key FROM projects pr WHERE pr.id = t.project_id) AS project,
       t.status, t.parked_until, t.parked_reason, t.priority, t.title, l.name AS lead, t.origin,
       (SELECT t2.team_key || '/' || m.name
          FROM proposals p JOIN members m ON m.id = p.origin_member_id JOIN tickets t2 ON t2.id = m.ticket_id
         WHERE p.id = t.proposal_id) AS proposed_by,
       t.created_at, t.updated_at
  FROM tickets t LEFT JOIN members l ON l.id = t.lead_id
 ORDER BY CASE t.status WHEN 'active' THEN 1 WHEN 'queued' THEN 2 WHEN 'parked' THEN 3 WHEN 'done' THEN 4 ELSE 5 END,
          t.priority, t.id DESC;

CREATE VIEW v_fleet AS                            -- what Fleet.base shows
SELECT t.key AS ticket, (SELECT pr.key FROM projects pr WHERE pr.id = t.project_id) AS project,
       t.team_key, m.lineage AS id, m.name, m.persona, m.agent_type, m.model,
       m.resolved_model, m.status, COALESCE(p.name, 'Spud') AS parent,
       m.spawned_at, m.finished_at, m.total_tokens, m.duration_ms, m.tool_uses
  FROM members m JOIN tickets t ON t.id = m.ticket_id LEFT JOIN members p ON p.id = m.parent_id
 ORDER BY t.id DESC, m.lineage;

CREATE VIEW v_live AS SELECT count(*) AS live FROM members WHERE status IN ('planned','active');

-- Append-only, with one exception: an event a subagent recorded before its agent_id was
-- bound to a member (SPD-008) may have member_id and ticket_id attached, once, by the
-- hook that lands the binding.  Every other column stays as written.
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
  WHEN NOT (OLD.member_id IS NULL AND NEW.member_id IS NOT NULL
            AND NEW.id = OLD.id AND NEW.at = OLD.at AND NEW.actor = OLD.actor
            AND (OLD.ticket_id IS NULL OR NEW.ticket_id IS OLD.ticket_id)
            AND NEW.agent_id IS OLD.agent_id AND NEW.kind = OLD.kind
            AND NEW.body = OLD.body AND NEW.data IS OLD.data)
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
"""

# Cross-repository projects (SPD-014, docs/design/2026-09-14-cross-repository-projects.md section 8): five columns on
# projects, the sessions a /spud claim makes Spud's, and seven event kinds.  SQLite cannot alter a CHECK, so events is
# rebuilt with the kind list widened; its triggers are dropped first and VIEWS_AND_TRIGGERS re-creates them after.
# The two views are dropped first as well (SPD-096): apply_migrations runs the newest VIEWS_AND_TRIGGERS after every
# migration, so on a fresh init v_board already names the parked columns 0003 has not added yet, and the rename below
# re-parses every view -- a rename fails on a view whose SELECT no longer resolves, whatever table is renamed.
DDL_0002 = """
DROP VIEW IF EXISTS v_board;
DROP VIEW IF EXISTS v_fleet;
ALTER TABLE projects ADD COLUMN default_branch TEXT NOT NULL DEFAULT 'main';
ALTER TABLE projects ADD COLUMN landing  TEXT NOT NULL DEFAULT 'merge'  CHECK (landing  IN ('merge','pr'));
ALTER TABLE projects ADD COLUMN sessions TEXT NOT NULL DEFAULT 'always' CHECK (sessions IN ('always','claim'));
ALTER TABLE projects ADD COLUMN installed TEXT CHECK (installed IS NULL OR json_valid(installed));
ALTER TABLE projects ADD COLUMN archived_at TEXT;

CREATE TABLE sessions (                           -- claims (/spud); a home session needs none
  session_id  TEXT    PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  claimed_at  TEXT    NOT NULL,
  released_at TEXT,
  cwd         TEXT
) STRICT;

DROP TRIGGER IF EXISTS events_no_update;
DROP TRIGGER IF EXISTS events_no_delete;
CREATE TABLE events_new (
  id        INTEGER PRIMARY KEY,
  at        TEXT    NOT NULL,
  actor     TEXT    NOT NULL,
  ticket_id INTEGER REFERENCES tickets(id),
  member_id INTEGER REFERENCES members(id),
  agent_id  TEXT,
  kind      TEXT    NOT NULL CHECK (kind IN (
              'ticket.created','ticket.status','ticket.priority','ticket.edited',
              'member.planned','member.spawn_denied','member.spawned','member.started','member.stopped',
              'member.log','member.result','member.blocked','member.outcome','member.status','member.edited',
              'handoff','proposal.filed','proposal.decided','hook.denied','hook.error','render',
              'report.entry','commit','import','config.synced',
              'project.added','project.edited','project.installed','project.uninstalled','project.removed',
              'session.claimed','session.released')),
  body      TEXT    NOT NULL DEFAULT '',
  data      TEXT    CHECK (data IS NULL OR json_valid(data))
) STRICT;
INSERT INTO events_new (id, at, actor, ticket_id, member_id, agent_id, kind, body, data)
  SELECT id, at, actor, ticket_id, member_id, agent_id, kind, body, data FROM events;
DROP TABLE events;
ALTER TABLE events_new RENAME TO events;
CREATE INDEX events_ticket ON events(ticket_id, id);
CREATE INDEX events_member ON events(member_id, id);
CREATE INDEX events_agent  ON events(agent_id, id);
CREATE INDEX events_kind   ON events(kind, id);
"""

# Parked tickets (SPD-096, docs/design/2026-09-16-parked-tickets.md section 4.1): a fifth status and the two columns
# that qualify it.  SQLite cannot alter a CHECK, so tickets is rebuilt; the two views that name it are dropped first
# (the rename re-parses every view, and one naming a missing table fails it), and VIEWS_AND_TRIGGERS re-creates them.
# apply_migrations turns foreign keys off around the transaction: with them on, DROP TABLE tickets is refused because
# members, events, handoffs, proposals and proposal_decisions point at it.
DDL_0003 = """
DROP VIEW IF EXISTS v_board;
DROP VIEW IF EXISTS v_fleet;
CREATE TABLE tickets_new (
  id          INTEGER PRIMARY KEY,                -- surrogate; the number lives in `number`
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  number      INTEGER NOT NULL,                   -- per-project counter: max(number) + 1 in the insert transaction
  key         TEXT    NOT NULL UNIQUE,            -- ticket_prefix || '-' || printf('%03d', number)
  team_key    TEXT    NOT NULL UNIQUE,            -- team_prefix   || '-' || printf('%03d', number)
  title       TEXT    NOT NULL,
  heading     TEXT,                               -- the H1 tail when the file shortens it against title
  priority    TEXT    NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
  status      TEXT    NOT NULL CHECK (status IN ('queued','active','parked','done','declined')),
  origin      TEXT    NOT NULL CHECK (origin IN ('eric','proposal')),
  proposal_id INTEGER REFERENCES proposals(id),   -- set when origin = 'proposal'; renders as proposed_by
  lead_id     INTEGER REFERENCES members(id),     -- the first member; NULL until one is planned
  brief       TEXT    NOT NULL DEFAULT '',
  sizing      TEXT    NOT NULL DEFAULT '',        -- "Size, persona and model decision"
  outcome     TEXT    NOT NULL DEFAULT '',
  tags        TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
  layout      TEXT    CHECK (layout IS NULL OR json_valid(layout)),  -- imported file's key and section order; NULL = template
  created_at  TEXT    NOT NULL,
  updated_at  TEXT    NOT NULL,
  closed_at   TEXT,
  parked_until  TEXT  CHECK (parked_until IS NULL OR (status = 'parked' AND parked_until GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')),
                                                  -- YYYY-MM-DD from which the brief board shows the ticket as due back; NULL = until moved
  parked_reason TEXT  CHECK ((parked_reason IS NOT NULL) = (status = 'parked')),   -- why it is parked; NULL unless parked
  UNIQUE (project_id, number)
) STRICT;
INSERT INTO tickets_new (id, project_id, number, key, team_key, title, heading, priority, status, origin, proposal_id, lead_id,
                         brief, sizing, outcome, tags, layout, created_at, updated_at, closed_at)
  SELECT id, project_id, number, key, team_key, title, heading, priority, status, origin, proposal_id, lead_id,
         brief, sizing, outcome, tags, layout, created_at, updated_at, closed_at FROM tickets;
DROP TABLE tickets;
ALTER TABLE tickets_new RENAME TO tickets;
CREATE INDEX tickets_board ON tickets(status, priority);
"""

MIGRATIONS = [("0001_init", DDL_0001), ("0002_projects", DDL_0002), ("0003_parked", DDL_0003)]
SCHEMA_VERSION = len(MIGRATIONS)
