CREATE TABLE metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE entities (
  entity_key TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  subject TEXT, session TEXT, datatype TEXT, task TEXT, run TEXT,
  acquisition TEXT, echo TEXT, suffix TEXT
);
CREATE TABLE stage_attempts (
  stage TEXT NOT NULL, scope TEXT NOT NULL, attempt INTEGER NOT NULL,
  state TEXT NOT NULL, log_path TEXT, started_at TEXT, finished_at TEXT,
  input_commit TEXT, output_commit TEXT, result_branch TEXT, job_id TEXT, error TEXT,
  PRIMARY KEY (stage, scope, attempt)
);
CREATE TABLE findings (
  id INTEGER PRIMARY KEY,
  entity_key TEXT REFERENCES entities(entity_key),
  finding_type TEXT NOT NULL, severity TEXT NOT NULL,
  evidence_path TEXT NOT NULL, evidence_json TEXT NOT NULL,
  UNIQUE (entity_key, finding_type, evidence_path)
);
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY,
  entity_key TEXT REFERENCES entities(entity_key),
  scope TEXT NOT NULL, decision TEXT NOT NULL, reviewer TEXT, reason TEXT, reviewed_at TEXT,
  UNIQUE (entity_key, scope)
);
CREATE TABLE artifacts (
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL, path TEXT NOT NULL,
  entity_key TEXT REFERENCES entities(entity_key), kind TEXT, commit_hash TEXT,
  UNIQUE (stage, path)
);
CREATE TABLE artifact_versions (
  id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, path TEXT NOT NULL,
  content_id TEXT NOT NULL, source_ids TEXT NOT NULL
);
CREATE TABLE artifact_observations (
  artifact_id TEXT NOT NULL REFERENCES artifact_versions(id),
  commit_hash TEXT, availability TEXT NOT NULL,
  PRIMARY KEY (artifact_id, commit_hash, availability)
);
CREATE TABLE processing_attempts (
  id TEXT PRIMARY KEY, stage TEXT NOT NULL, scope TEXT NOT NULL,
  status TEXT NOT NULL, evidence_json TEXT NOT NULL
);
CREATE TABLE lineage_links (
  input TEXT NOT NULL REFERENCES artifact_versions(id),
  attempt TEXT NOT NULL REFERENCES processing_attempts(id),
  output TEXT NOT NULL REFERENCES artifact_versions(id), relation TEXT NOT NULL,
  PRIMARY KEY (input, attempt, output, relation)
);
