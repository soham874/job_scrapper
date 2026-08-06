-- V011: Map the six Oracle Recruiting Cloud companies to the oracle borg
--
-- All six rows already exist from V002 with an empty ats, so these are updates.
-- Links are normalised to the canonical CandidateExperience form. The scraper
-- only needs the host and the site number, both of which it parses back out.
--
-- Note: Goldman Sachs is on CX_2. Its CX_1 site returns zero requisitions.

UPDATE company_info SET ats = 'oracle', ats_link = 'https://ebcs.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions' WHERE company_name = 'ExxonMobil';
UPDATE company_info SET ats = 'oracle', ats_link = 'https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions' WHERE company_name = 'Ford';
UPDATE company_info SET ats = 'oracle', ats_link = 'https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/requisitions' WHERE company_name = 'Goldman Sachs';
UPDATE company_info SET ats = 'oracle', ats_link = 'https://ibqbjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions' WHERE company_name = 'Honeywell';
UPDATE company_info SET ats = 'oracle', ats_link = 'https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions' WHERE company_name = 'JP Morgan';
UPDATE company_info SET ats = 'oracle', ats_link = 'https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions' WHERE company_name = 'Oracle';
