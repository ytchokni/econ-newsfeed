-- Migration 001: Rename publications → papers; add status, draft_url columns;
--               create research_fields and researcher_fields tables; seed taxonomy.
--
-- Run this against the database BEFORE deploying the updated application code.

-- 1. Rename table
ALTER TABLE publications RENAME TO papers;

-- 2. Add new columns to papers
ALTER TABLE papers
    ADD COLUMN status ENUM('published', 'accepted', 'revise_and_resubmit', 'reject_and_resubmit') DEFAULT NULL,
    ADD COLUMN draft_url VARCHAR(2048) DEFAULT NULL,
    ADD INDEX idx_status (status);

-- 3. Create research_fields taxonomy table
CREATE TABLE IF NOT EXISTS research_fields (
    id   INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    UNIQUE KEY uq_slug (slug)
);

-- 4. Create researcher ↔ field join table
CREATE TABLE IF NOT EXISTS researcher_fields (
    researcher_id INT NOT NULL,
    field_id      INT NOT NULL,
    PRIMARY KEY (researcher_id, field_id),
    FOREIGN KEY (researcher_id) REFERENCES researchers(id),
    FOREIGN KEY (field_id)      REFERENCES research_fields(id)
);

-- 5. Seed initial taxonomy
INSERT IGNORE INTO research_fields (name, slug) VALUES
    ('Macroeconomics',        'macroeconomics'),
    ('Labour Economics',      'labour-economics'),
    ('Cultural Economics',    'cultural-economics'),
    ('Migration',             'migration'),
    ('Political Economy',     'political-economy'),
    ('Development Economics', 'development-economics'),
    ('International Trade',   'international-trade'),
    ('Finance',               'finance'),
    ('Health Economics',      'health-economics'),
    ('Public Economics',      'public-economics'),
    ('Industrial Organisation','industrial-organisation'),
    ('Econometrics/Methods',  'econometrics-methods');
