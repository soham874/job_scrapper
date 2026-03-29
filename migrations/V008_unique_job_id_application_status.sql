-- V008: Add unique constraint on job_id in application_status to prevent duplicate entries

ALTER TABLE application_status ADD CONSTRAINT uq_application_status_job_id UNIQUE (job_id);
