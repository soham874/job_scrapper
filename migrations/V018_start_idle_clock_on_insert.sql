-- V018: Start the idle clock when the application row is written.
--
-- V015 added updated_at as `TIMESTAMP NULL DEFAULT NULL ON UPDATE
-- CURRENT_TIMESTAMP` and backfilled the rows that existed then. What it did
-- not do is give the column a default, so every row inserted since — every
-- job accepted from a Telegram card — has landed with updated_at NULL and
-- stayed there until the user happened to open its menu.
--
-- A NULL there is not a small gap. DATEDIFF(NOW(), NULL) is NULL, so the
-- staleness half of the reminder query never matched those rows and the
-- dashboard had no idle figure to show for them. The one state that most
-- needs the clock running — applied, and untouched since — was the one state
-- with no clock at all.
--
-- Applying alone is activity, so the row's own creation is what starts the
-- timer. Rows still carrying NULL fall back to applied_on, which is the
-- closest record of when that activity happened.

UPDATE application_status SET updated_at = COALESCE(applied_on, NOW()) WHERE updated_at IS NULL;

-- NOT NULL so the column cannot silently go back to having no clock, and
-- DEFAULT CURRENT_TIMESTAMP so a plain INSERT starts it. ON UPDATE is kept
-- from V015 — MODIFY rewrites the whole definition, so leaving it out would
-- drop the auto-touch the tracker relies on.
ALTER TABLE application_status
    MODIFY COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
