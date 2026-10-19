-- Jarvis DB 스키마 원본 (상세 설계 7장). db.py 가 시작할 때 실행한다.
-- 스키마를 바꾸면 SCHEMA_VERSION(db.py)을 올리고, 개발 중에는 hub.db 를 지우고 다시 만든다.
-- 이름과 영상은 저장하지 않는다. 학생은 좌석 번호로만 구분한다.

-- ---- 기존 표 -------------------------------------------------------------
-- 노드별 접속 PC (호스트 이름, IP, OS)
CREATE TABLE IF NOT EXISTS nodes (
  node TEXT PRIMARY KEY, hostname TEXT, ip TEXT, peer TEXT, os TEXT,
  python TEXT, pid INTEGER, connected_ts REAL, updated_ts REAL);

-- 노드가 보낸 원본 메시지 (heartbeat, track_offset 은 기록하지 않음)
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, node TEXT, seq INTEGER, type TEXT, seat INTEGER,
  ts REAL, rx_ts REAL, value TEXT, conf REAL);

-- 노드별 최신 상태 (하트비트)
CREATE TABLE IF NOT EXISTS node_status (
  node TEXT PRIMARY KEY, last_ts REAL, fps REAL, cpu REAL, clock_offset_s REAL,
  online INTEGER, cam1_fps REAL, cam2_fps REAL);

-- ---- 이벤트 관리 ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS seat (
  seat       INTEGER PRIMARY KEY,         -- 1~4
  label      TEXT,
  pan        REAL, tilt REAL,             -- 프리셋 각도
  neighbors  TEXT                         -- "2,4"
);

CREATE TABLE IF NOT EXISTS help_event (
  event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  seat          INTEGER NOT NULL REFERENCES seat(seat),
  kind          TEXT NOT NULL,            -- fall | question | away
  priority      INTEGER NOT NULL,
  status        TEXT NOT NULL,            -- active | waiting | closed
  detected_at   REAL NOT NULL,            -- Control 시계
  confirmed_at  REAL, confirmed_by TEXT,
  closed_at     REAL, close_reason TEXT,  -- resolved | expired | cleared
  alert_stage   TEXT DEFAULT 'none',      -- none | neighbor | all
  explain_text  TEXT, explain_mode TEXT,
  source        TEXT                      -- "vision_service:1042"
);

CREATE TABLE IF NOT EXISTS event_log (
  log_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id  INTEGER NOT NULL REFERENCES help_event(event_id),
  ts        REAL NOT NULL,                -- Control 시계
  action    TEXT NOT NULL,                -- detect | activate | preempt | wait | confirm | alert | resolve | expire | clear
  actor     TEXT NOT NULL,                -- system | instructor_console | student_client_N
  detail    TEXT                          -- JSON
);
