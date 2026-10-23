-- data/schema.sql

-- Clear old schema if present
DROP TABLE IF EXISTS tissue_loads_logged CASCADE;
DROP TABLE IF EXISTS athlete_sessions CASCADE;
DROP TABLE IF EXISTS body_parts CASCADE;
DROP TABLE IF EXISTS athletes CASCADE;

-- 1. Athletes Table
CREATE TABLE athletes (
    athlete_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    experience_level VARCHAR(20) DEFAULT 'INTERMEDIATE' CHECK (experience_level IN ('NOVICE', 'INTERMEDIATE', 'ADVANCED')),
    primary_goal VARCHAR(100) DEFAULT 'General Conditioning',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Body Parts Dictionary Table
CREATE TABLE body_parts (
    body_part_id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    tissue_type VARCHAR(20) NOT NULL CHECK (tissue_type IN ('TENDON', 'MUSCLE'))
);

-- 3. Athlete Sessions Table
CREATE TABLE athlete_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    athlete_id VARCHAR(50) NOT NULL REFERENCES athletes(athlete_id) ON DELETE CASCADE,
    session_date DATE NOT NULL,
    session_title VARCHAR(200),
    raw_input TEXT NOT NULL,
    session_rpe NUMERIC(3,1) CHECK (session_rpe BETWEEN 1.0 AND 10.0),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Tissue Loads Logged (M:N Associative Table)
CREATE TABLE tissue_loads_logged (
    load_id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES athlete_sessions(session_id) ON DELETE CASCADE,
    body_part_id INTEGER NOT NULL REFERENCES body_parts(body_part_id) ON DELETE RESTRICT,
    stress_score NUMERIC(3,1) NOT NULL CHECK (stress_score BETWEEN 1.0 AND 10.0),
    volume_mins INTEGER DEFAULT 0,
    CONSTRAINT unique_session_body_part UNIQUE (session_id, body_part_id)
);

-- Seed Default Athlete Profile
INSERT INTO athletes (athlete_id, name, experience_level)
VALUES ('athlete_001', 'Default Athlete', 'INTERMEDIATE');

-- Seed Standard Muscle & Tendon Dictionary
INSERT INTO body_parts (name, tissue_type) VALUES
    -- TENDONS & CONNECTIVE TISSUE
    ('Patellar Tendon', 'TENDON'),
    ('Achilles Tendon', 'TENDON'),
    ('Rotator Cuff Tendons', 'TENDON'),
    ('Distal Biceps Tendon', 'TENDON'),
    ('Triceps Brachii Tendon', 'TENDON'),
    ('Hamstring Tendons', 'TENDON'),
    ('Plantar Fascia', 'TENDON'),
    ('Iliotibial (IT) Band', 'TENDON'),
    ('Common Extensor Tendon (Lateral Epicondyle)', 'TENDON'),
    ('Common Flexor Tendon (Medial Epicondyle)', 'TENDON'),

    -- LOWER EXTREMITY MUSCLES
    ('Quadriceps Femoris', 'MUSCLE'),
    ('Hamstrings (Biceps Femoris / Semitendinosus / Semimembranosus)', 'MUSCLE'),
    ('Gastrocnemius & Soleus', 'MUSCLE'),
    ('Tibialis Anterior', 'MUSCLE'),
    ('Gluteus Maximus', 'MUSCLE'),
    ('Gluteus Medius & Minimus', 'MUSCLE'),
    ('Hip Adductors (Adductor Longus / Magnus / Brevis)', 'MUSCLE'),
    ('Iliopsoas (Hip Flexors)', 'MUSCLE'),

    -- UPPER EXTREMITY & SHOULDER MUSCLES
    ('Pectoralis Major & Minor', 'MUSCLE'),
    ('Latissimus Dorsi', 'MUSCLE'),
    ('Trapezius (Upper / Middle / Lower)', 'MUSCLE'),
    ('Rhomboids', 'MUSCLE'),
    ('Deltoids (Anterior / Lateral / Posterior)', 'MUSCLE'),
    ('Biceps Brachii & Brachialis', 'MUSCLE'),
    ('Triceps Brachii', 'MUSCLE'),
    ('Wrist Flexors & Extensors (Forearms)', 'MUSCLE'),

    -- CORE & AXIAL MUSCLES
    ('Rectus Abdominis', 'MUSCLE'),
    ('Obliques (Internal / External)', 'MUSCLE'),
    ('Erector Spinae', 'MUSCLE'),
    ('Quadratus Lumborum', 'MUSCLE');