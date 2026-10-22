CREATE TABLE exercise_catalog (
    exercise_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    movement_category VARCHAR(20) NOT NULL, -- PUSH, PULL, LEGS, CORE
    target_muscle VARCHAR(50) NOT NULL,
    spinal_axial_load_score NUMERIC(3, 1) NOT NULL, -- Scale 0.0 - 10.0
    tendon_stress_score NUMERIC(3, 1) NOT NULL,     -- Scale 0.0 - 10.0
    cns_fatigue_index NUMERIC(3, 1) NOT NULL
);

CREATE TABLE athlete_workouts (
    workout_id VARCHAR(50) PRIMARY KEY,
    athlete_id VARCHAR(50) NOT NULL DEFAULT 'athlete_001',
    workout_date DATE NOT NULL,
    title VARCHAR(100) NOT NULL,
    total_volume_kg NUMERIC(10, 2) DEFAULT 0.0,
    acwr_at_execution NUMERIC(4, 2),
    status VARCHAR(20) DEFAULT 'COMPLETED' -- 'COMPLETED', 'PROPOSED', 'REJECTED'
);

CREATE TABLE workout_sets (
    set_id SERIAL PRIMARY KEY,
    workout_id VARCHAR(50) REFERENCES athlete_workouts(workout_id) ON DELETE CASCADE,
    exercise_id VARCHAR(50) REFERENCES exercise_catalog(exercise_id),
    reps INT NOT NULL,
    weight_kg NUMERIC(6, 2) NOT NULL,
    rpe NUMERIC(3, 1)
);

CREATE TABLE athlete_recovery_windows (
    window_id SERIAL PRIMARY KEY,
    athlete_id VARCHAR(50) NOT NULL DEFAULT 'athlete_001',
    source_workout_id VARCHAR(50) REFERENCES athlete_workouts(workout_id),
    window_type VARCHAR(30) NOT NULL, -- 'TENDON_COLLAGEN', 'SPINAL_AXIAL', 'ACWR_SPIKE'
    trigger_date DATE NOT NULL,
    safe_date DATE NOT NULL,            -- Date when lockout expires
    status_color VARCHAR(10) NOT NULL,   -- 'RED', 'AMBER', 'GREEN'
    exercise_cause VARCHAR(100),
    message TEXT NOT NULL
);


-- Insert Baseline Movement Profiles
INSERT INTO exercise_catalog (exercise_id, name, movement_category, target_muscle, spinal_axial_load_score, tendon_stress_score, cns_fatigue_index) VALUES
('barbell_back_squat', 'Barbell Back Squat', 'LEGS', 'Quadriceps', 8.5, 7.5, 8.0),
('barbell_deadlift', 'Barbell Deadlift', 'PULL', 'Erector Spinae / Hamstrings', 9.5, 6.0, 9.0),
('barbell_bench_press', 'Barbell Bench Press', 'PUSH', 'Pectoralis Major', 3.0, 8.0, 6.5),
('overhead_press', 'Standing Overhead Press', 'PUSH', 'Anterior Deltoid', 7.0, 6.5, 7.0),
('pull_ups', 'Weighted Pull-Ups', 'PULL', 'Latissimus Dorsi', 2.0, 7.5, 6.0),
('romanian_deadlift', 'Romanian Deadlift', 'LEGS', 'Hamstrings / Glutes', 7.5, 8.0, 7.5),
('face_pulls', 'Cable Face Pulls', 'PULL', 'Rear Deltoid / Rotator Cuff', 1.0, 2.0, 2.0)
ON CONFLICT (exercise_id) DO NOTHING;