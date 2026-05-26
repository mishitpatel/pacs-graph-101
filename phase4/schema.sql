-- phase4/schema.sql — SQLite mirror of the PACS graph.
--
-- Same data as graph.json, expressed in the relational shape a real
-- production PACS would use: one entity table per class, one junction
-- table per predicate. Policy-edge junction tables carry valid_from /
-- valid_to columns; infrastructure-edge tables don't.
--
-- This file is idempotent on import — every table is dropped first.

-- Drop in dependency order (junction → entity).
DROP TABLE IF EXISTS managed_by;
DROP TABLE IF EXISTS protects;
DROP TABLE IF EXISTS controls;
DROP TABLE IF EXISTS active_during;
DROP TABLE IF EXISTS grants_access;
DROP TABLE IF EXISTS membership;
DROP TABLE IF EXISTS holds;
DROP TABLE IF EXISTS controller;
DROP TABLE IF EXISTS zone;
DROP TABLE IF EXISTS door;
DROP TABLE IF EXISTS reader;
DROP TABLE IF EXISTS schedule;
DROP TABLE IF EXISTS access_group;
DROP TABLE IF EXISTS credential;
DROP TABLE IF EXISTS person;

-- ============================================================================
-- Entity tables
-- ============================================================================

CREATE TABLE person      (id TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE credential  (id TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE access_group(id TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE reader      (id TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE door        (id TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE zone        (id TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE controller  (id TEXT PRIMARY KEY, label TEXT NOT NULL);

-- Schedule carries its semantics as columns — same shape as the properties
-- we attach to Schedule nodes in Neo4j during import.
CREATE TABLE schedule (
    id          TEXT    PRIMARY KEY,
    label       TEXT    NOT NULL,
    weekdays    TEXT    NOT NULL,     -- JSON array, ISO 8601: 1=Mon..7=Sun
    start_hour  INTEGER NOT NULL,
    end_hour    INTEGER NOT NULL
);

-- ============================================================================
-- Temporal junction tables — INTEGER PK lets the same (a, b) pair appear
-- multiple times with non-overlapping (valid_from, valid_to) windows.
-- This is what makes re-instatement modelable: Bob's two ContractorsDay
-- tenures are two separate rows in `membership`.
-- ============================================================================

CREATE TABLE holds (
    id            INTEGER PRIMARY KEY,
    person_id     TEXT NOT NULL REFERENCES person(id),
    credential_id TEXT NOT NULL REFERENCES credential(id),
    valid_from    DATE,
    valid_to      DATE
);

CREATE TABLE membership (
    id         INTEGER PRIMARY KEY,
    person_id  TEXT NOT NULL REFERENCES person(id),
    group_id   TEXT NOT NULL REFERENCES access_group(id),
    valid_from DATE,
    valid_to   DATE
);

CREATE TABLE grants_access (
    id         INTEGER PRIMARY KEY,
    group_id   TEXT NOT NULL REFERENCES access_group(id),
    door_id    TEXT NOT NULL REFERENCES door(id),
    valid_from DATE,
    valid_to   DATE
);

CREATE TABLE active_during (
    id          INTEGER PRIMARY KEY,
    group_id    TEXT NOT NULL REFERENCES access_group(id),
    schedule_id TEXT NOT NULL REFERENCES schedule(id),
    valid_from  DATE,
    valid_to    DATE
);

-- ============================================================================
-- Infrastructure junction tables — static, composite PK is the natural key.
-- ============================================================================

CREATE TABLE controls (
    reader_id TEXT NOT NULL REFERENCES reader(id),
    door_id   TEXT NOT NULL REFERENCES door(id),
    PRIMARY KEY (reader_id, door_id)
);

CREATE TABLE protects (
    door_id TEXT NOT NULL REFERENCES door(id),
    zone_id TEXT NOT NULL REFERENCES zone(id),
    PRIMARY KEY (door_id, zone_id)
);

CREATE TABLE managed_by (
    reader_id     TEXT NOT NULL REFERENCES reader(id),
    controller_id TEXT NOT NULL REFERENCES controller(id),
    PRIMARY KEY (reader_id, controller_id)
);

-- Indices on the temporal junction tables — small data here, but real
-- systems would benefit from these for "as-of" lookups and joins.
CREATE INDEX idx_membership_person   ON membership(person_id);
CREATE INDEX idx_membership_group    ON membership(group_id);
CREATE INDEX idx_grants_access_door  ON grants_access(door_id);
CREATE INDEX idx_grants_access_group ON grants_access(group_id);
CREATE INDEX idx_holds_person        ON holds(person_id);
