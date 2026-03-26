-- V006: Drop description column from job_info (analysis is stored in job_analysis)

ALTER TABLE job_info DROP COLUMN IF EXISTS description;
