-- V015: Make application_status writable as a tracker, not just an insert log.
--
-- Until now the only row this table ever saw was the one handle_apply() writes
-- at accept time: company_id, job_id, applied_on, status='applied'. The other
-- columns from V001 (poc, next_important_date, next_important_task) have never
-- had a writer, so every one of them is NULL on every row.
--
-- The Telegram menu changes that — status moves through a vocabulary and the
-- user sets a follow-up date — which needs two things the current shape cannot
-- give:
--
--   * Real DATE columns. applied_on and next_important_date are VARCHAR(255)
--     holding date.today().isoformat(). String comparison happens to sort
--     correctly for ISO text, but the reminder query wants
--     `next_important_date <= CURDATE()` and the job card wants DATEDIFF, and
--     neither is safe or indexable against a VARCHAR.
--
--   * A last-touched timestamp. Explicit reminders only fire for follow-ups
--     the user remembered to set. The more useful signal is silence — an
--     application sitting in 'screening' for two weeks — and that needs to
--     know when the row last moved.
--
-- The two UPDATEs below are defensive. Nothing has ever written a non-ISO
-- value into either column, but an unparseable string would abort the ALTER
-- under strict mode, and NULLing it is better than failing the migration.

UPDATE application_status SET applied_on = NULL
    WHERE applied_on IS NOT NULL AND applied_on NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$';

UPDATE application_status SET next_important_date = NULL
    WHERE next_important_date IS NOT NULL AND next_important_date NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$';

ALTER TABLE application_status MODIFY COLUMN applied_on DATE NULL DEFAULT NULL;

ALTER TABLE application_status MODIFY COLUMN next_important_date DATE NULL DEFAULT NULL;

ALTER TABLE application_status
    ADD COLUMN updated_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP;

-- When the reminder loop last nudged about this row. Without it the loop has
-- no memory: a follow-up that came due, or a status that went quiet, matches
-- its query on every pass and would be re-sent every time the loop wakes.
-- The state has to live here rather than in the process, because the process
-- restarts and the nagging would start over.
ALTER TABLE application_status
    ADD COLUMN last_notified_at TIMESTAMP NULL DEFAULT NULL;

-- Seed the new column so existing rows have a sane age instead of NULL.
-- Assigning it explicitly suppresses the ON UPDATE clause for this statement.
UPDATE application_status SET updated_at = COALESCE(applied_on, CURDATE());

-- Drives the reminder sweep: WHERE next_important_date <= CURDATE().
CREATE INDEX idx_app_status_next_date ON application_status (next_important_date);

-- Drives /active (filter by status) and the staleness check (order by age).
CREATE INDEX idx_app_status_status_updated ON application_status (status, updated_at);
