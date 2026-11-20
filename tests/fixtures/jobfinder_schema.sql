CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL UNIQUE,
  source TEXT, title TEXT, company TEXT, location TEXT, details TEXT,
  family TEXT, city TEXT, remote INTEGER, pay_text TEXT, pay_top REAL,
  salary_unknown INTEGER, driving TEXT, verdict TEXT, reasons TEXT,
  status TEXT, posted TEXT, first_seen TEXT, last_seen TEXT,
  dupkey TEXT, dup_of INTEGER, picked_on TEXT, pick_city TEXT, needs_details INTEGER
);
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE source_runs (
  name TEXT PRIMARY KEY, ok INTEGER, count INTEGER, error TEXT, ran_at TEXT
);
PRAGMA user_version = 0;
