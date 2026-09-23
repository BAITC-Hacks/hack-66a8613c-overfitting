PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, meeting_date TEXT, timezone TEXT,
    participants_json TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
    machine_prepared INTEGER NOT NULL DEFAULT 1 CHECK(machine_prepared IN (0,1)), approved_at TEXT
);
CREATE TABLE IF NOT EXISTS source_files (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL, storage_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 0 AND 209715200),
    duration_seconds REAL CHECK(duration_seconds BETWEEN 0 AND 900)
);
CREATE TABLE IF NOT EXISTS processing_jobs (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('queued','preparing_audio','ready_for_models','transcribing','diarizing','saving_transcript','analyzing','ready','failed')),
    stage TEXT, error_code TEXT, audio_path TEXT, detected_language TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_processing_job ON processing_jobs((1))
WHERE status IN ('preparing_audio','transcribing','diarizing','saving_transcript','analyzing') OR (status='ready_for_models' AND stage='models');
CREATE TABLE IF NOT EXISTS speakers (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    label TEXT NOT NULL, name TEXT, UNIQUE(meeting_id, id), UNIQUE(meeting_id, label)
);
CREATE TABLE IF NOT EXISTS utterances (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker_id TEXT, start_seconds REAL NOT NULL CHECK(start_seconds >= 0),
    end_seconds REAL NOT NULL CHECK(end_seconds >= start_seconds), text TEXT NOT NULL,
    requires_review INTEGER NOT NULL CHECK(requires_review IN (0,1)), UNIQUE(meeting_id, id),
    FOREIGN KEY(meeting_id, speaker_id) REFERENCES speakers(meeting_id, id)
);
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK(position >= 1), title TEXT NOT NULL, summary TEXT NOT NULL,
    UNIQUE(meeting_id, id), UNIQUE(meeting_id, position)
);
CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL, utterance_id TEXT NOT NULL, text TEXT NOT NULL,
    FOREIGN KEY(meeting_id, topic_id) REFERENCES topics(meeting_id, id),
    FOREIGN KEY(meeting_id, utterance_id) REFERENCES utterances(meeting_id, id)
);
CREATE TABLE IF NOT EXISTS problems (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL, utterance_id TEXT NOT NULL, text TEXT NOT NULL,
    FOREIGN KEY(meeting_id, topic_id) REFERENCES topics(meeting_id, id),
    FOREIGN KEY(meeting_id, utterance_id) REFERENCES utterances(meeting_id, id)
);
CREATE TABLE IF NOT EXISTS action_items (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL, text TEXT NOT NULL CHECK(length(text) > 0),
    responsible TEXT NOT NULL DEFAULT 'не указан' CHECK(length(responsible) > 0),
    deadline_original TEXT NOT NULL DEFAULT 'не указан' CHECK(length(deadline_original) > 0),
    deadline_date TEXT, utterance_id TEXT NOT NULL,
    timestamp_seconds REAL NOT NULL CHECK(timestamp_seconds >= 0),
    requires_review INTEGER NOT NULL CHECK(requires_review IN (0,1)),
    FOREIGN KEY(meeting_id, topic_id) REFERENCES topics(meeting_id, id),
    FOREIGN KEY(meeting_id, utterance_id) REFERENCES utterances(meeting_id, id)
);
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    format TEXT NOT NULL CHECK(format IN ('docx','pdf')), storage_path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    requires_review INTEGER NOT NULL CHECK(requires_review IN (0,1))
);
CREATE TABLE IF NOT EXISTS key_points (
    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK(position >= 1), direction TEXT NOT NULL,
    metric TEXT NOT NULL, problem TEXT NOT NULL,
    requires_review INTEGER NOT NULL CHECK(requires_review IN (0,1)),
    UNIQUE(meeting_id,id), UNIQUE(meeting_id,position)
);
CREATE TABLE IF NOT EXISTS topic_sources (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL, utterance_id TEXT NOT NULL,
    PRIMARY KEY(meeting_id,topic_id,utterance_id),
    FOREIGN KEY(meeting_id,topic_id) REFERENCES topics(meeting_id,id) ON DELETE CASCADE,
    FOREIGN KEY(meeting_id,utterance_id) REFERENCES utterances(meeting_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS action_sources (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL REFERENCES action_items(id) ON DELETE CASCADE,
    utterance_id TEXT NOT NULL,
    PRIMARY KEY(meeting_id,action_id,utterance_id),
    FOREIGN KEY(meeting_id,action_id) REFERENCES action_items(meeting_id,id) ON DELETE CASCADE,
    FOREIGN KEY(meeting_id,utterance_id) REFERENCES utterances(meeting_id,id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS action_meeting_id ON action_items(meeting_id,id);
CREATE TABLE IF NOT EXISTS key_point_sources (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    key_point_id TEXT NOT NULL, utterance_id TEXT NOT NULL,
    PRIMARY KEY(meeting_id,key_point_id,utterance_id),
    FOREIGN KEY(meeting_id,key_point_id) REFERENCES key_points(meeting_id,id) ON DELETE CASCADE,
    FOREIGN KEY(meeting_id,utterance_id) REFERENCES utterances(meeting_id,id) ON DELETE CASCADE
);
