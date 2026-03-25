-- V001: Create core tables
-- company_info, job_info, application_status

CREATE TABLE IF NOT EXISTS company_info (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_name VARCHAR(255) UNIQUE NOT NULL,
    base_country VARCHAR(255),
    target_location VARCHAR(255),
    ats VARCHAR(100),
    ats_link VARCHAR(1024)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS job_info (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    ats_job_id VARCHAR(255) UNIQUE NOT NULL,
    title VARCHAR(500),
    location VARCHAR(500),
    description TEXT,
    application_link VARCHAR(1024),
    FOREIGN KEY (company_id) REFERENCES company_info(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS application_status (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT,
    job_id INT,
    applied_on VARCHAR(255),
    poc VARCHAR(255),
    resume_link VARCHAR(1024),
    status VARCHAR(100),
    next_important_date VARCHAR(255),
    next_important_task VARCHAR(500),
    FOREIGN KEY (company_id) REFERENCES company_info(id),
    FOREIGN KEY (job_id) REFERENCES job_info(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
