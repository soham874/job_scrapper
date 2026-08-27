-- Configuration for the self_json borg.
--
-- Both columns are filled from the company sheet and only mean anything for
-- rows whose ATS is 'self_json'. They are TEXT rather than VARCHAR because a
-- browser-copied curl carries the full sec-ch-ua/sec-fetch header set and runs
-- well past the 1024 bytes ats_link allows.

ALTER TABLE company_info ADD COLUMN job_api_curl TEXT NULL;
ALTER TABLE company_info ADD COLUMN job_spec TEXT NULL;
