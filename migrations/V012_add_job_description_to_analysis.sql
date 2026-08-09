-- V012: Store the raw job description alongside the analysis.
--
-- V006 dropped description from job_info to keep that table lean. The resume
-- tailoring module needs the description text at accept time, so it lives here
-- instead — job_analysis is already the "derived data about a job" table, and
-- rows here are only created for jobs that cleared DESC_SCORE_THRESHOLD.

ALTER TABLE job_analysis ADD COLUMN job_description VARCHAR(10000) DEFAULT NULL;