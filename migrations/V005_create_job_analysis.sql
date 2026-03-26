-- V005: Create job_analysis table for description relevance scoring

CREATE TABLE IF NOT EXISTS job_analysis (
    id INT AUTO_INCREMENT PRIMARY KEY,
    job_id INT NOT NULL UNIQUE,
    relevance_score INT NOT NULL DEFAULT 0,
    positive_matches TEXT,
    negative_matches TEXT,
    experience_matches TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES job_info(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
