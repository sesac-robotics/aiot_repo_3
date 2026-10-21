-- Jarvis DB v2 스키마 원본 (Confluence "Data Structure" v2, 2026-10-21). db.py 가 시작할 때 실행한다.
-- DB(hub.db)는 Control 만 읽고 쓴다. 다른 프로그램은 메시지로만 받는다.
-- 상태값은 모두 Interface Spec 의 숫자 코드, 시각은 Control 시계 기준 Unix 초(REAL).
-- 스키마를 바꾸면 SCHEMA_VERSION(db.py)을 올리고, 개발 중에는 hub.db 를 지우고 다시 만든다 (마이그레이션 없음).
-- 영상은 도움 요청 이벤트 구간의 팬틸트 영상만 저장한다 (event_clip). 학생 개인 판정값은 저장하지 않는다.
-- 이전 구현의 messages, nodes, node_status 는 아래 테이블로 대체되었다 (heartbeat·관측은 기록하지 않음).

-- 좌석 (1~4, 번호 고정). 각도는 팬틸트 모듈 presets.json, 여기엔 등록 여부만
CREATE TABLE IF NOT EXISTS seat (
  seat          INTEGER PRIMARY KEY,          -- 1~4 (학생 노트북마다 고정)
  label         TEXT,                         -- 표시 이름 (예: "앞줄 왼쪽")
  neighbors     TEXT NOT NULL DEFAULT '',     -- 2×2 가로·세로: 1→"2,3" 2→"1,4" 3→"1,4" 4→"2,3"
  device_node   INTEGER REFERENCES device(node), -- 이 좌석 학생 노트북 (11~14)
  has_preset    INTEGER NOT NULL DEFAULT 0,   -- 팬틸트 각도 등록 여부
  reg_id        INTEGER REFERENCES seat_registration(reg_id) -- 지금 앉은 학생 (NULL = 미등록)
);

-- 자리 등록 이력 (학생 화면 [자리 등록]: 이름 + 바른 자세 10초)
CREATE TABLE IF NOT EXISTS seat_registration (
  reg_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  seat          INTEGER NOT NULL REFERENCES seat(seat),
  student_name  TEXT NOT NULL,                -- 실명 (강사 화면 좌석도·로그에 표시)
  baseline      TEXT NOT NULL,                -- JSON 7개: ear_shoulder, head_ratio, shoulder_y, shoulder_tilt, ear_tilt, yaw, pitch
  zones         TEXT,                         -- JSON 영역 캘리브레이션 (screen, edge_a, edge_b, desk의 yaw·pitch, 최대 8개)
  registered_at REAL NOT NULL,
  replaced_at   REAL                          -- 같은 좌석에 새로 등록하면 이전 행에 기록 (NULL = 현재)
);
CREATE INDEX IF NOT EXISTS ix_reg_seat ON seat_registration(seat, replaced_at);
CREATE INDEX IF NOT EXISTS ix_reg_name ON seat_registration(student_name);

-- 장비 (시설 관리 탭). TCP 노드가 아닌 장비도 상태 표시를 위해 가상 번호로 둔다
CREATE TABLE IF NOT EXISTS device (
  node        INTEGER PRIMARY KEY,            -- 0 control · 1 vision · 2 팬틸트(Control 내부, 가상) · 3 console
                                              -- 11~14 학생 노트북 · 21 고정 카메라 cam1 · 22 팬틸트 카메라 cam2 (가상)
  name        TEXT NOT NULL,
  kind        INTEGER NOT NULL,               -- 1 서버 · 2 카메라 · 3 팬틸트 · 4 학생 노트북 · 5 강사 화면
  location    TEXT,
  seat        INTEGER REFERENCES seat(seat),  -- 학생 노트북이면 좌석
  online      INTEGER NOT NULL DEFAULT 0,     -- TCP 노드: heartbeat / 카메라: 프레임 수신 / 팬틸트: state != SAFE
  last_seen   REAL,
  net_rtt_ms  REAL,                           -- 학생 정보 창 '네트워크' (heartbeat 왕복)
  cam_ok      INTEGER,                        -- 학생 정보 창 '노트북 카메라'
  note        TEXT                            -- 끊겼을 때 영향 설명 (예: "4번 좌석 쓰러짐 알림 못 받음")
);

-- 도움 요청 (쓰러짐·질문·자리비움)
CREATE TABLE IF NOT EXISTS help_event (
  event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  seat          INTEGER NOT NULL REFERENCES seat(seat),
  reg_id        INTEGER REFERENCES seat_registration(reg_id), -- 그 시각에 앉아 있던 학생 (학생별 검색)
  kind          INTEGER NOT NULL,             -- 1 fall · 2 question · 3 away
  priority      INTEGER NOT NULL,             -- = kind (1이 가장 높음)
  status        INTEGER NOT NULL,             -- 1 active · 2 waiting · 3 closed
  alert_stage   INTEGER NOT NULL DEFAULT 0,   -- 0 없음 · 1 neighbor · 2 all · 3 long
  close_reason  INTEGER NOT NULL DEFAULT 0,   -- 0 없음 · 1 resolved · 2 false_alarm · 3 expired · 4 cleared
  hand_down     INTEGER NOT NULL DEFAULT 0,
  detected_at   REAL NOT NULL,
  confirmed_at  REAL,                         -- NULL이면 '확인 안 한 이벤트' (이벤트 탭 배지 수)
  confirmed_by  INTEGER,                      -- 3 console · 11~14 student
  closed_at     REAL,
  gen           INTEGER,
  explain_text  TEXT,
  explain_mode  INTEGER NOT NULL DEFAULT 0    -- 0 없음 · 1 gemini · 2 ollama · 3 default
);
CREATE INDEX IF NOT EXISTS ix_event_open   ON help_event(status, priority, detected_at);
CREATE INDEX IF NOT EXISTS ix_event_seat   ON help_event(seat, kind, status);
CREATE INDEX IF NOT EXISTS ix_event_search ON help_event(detected_at, kind, reg_id);   -- 로그 검색 (기간·종류·학생)
CREATE INDEX IF NOT EXISTS ix_event_unconf ON help_event(confirmed_at, status);        -- 미확인 배지

-- 도움 요청 이력
CREATE TABLE IF NOT EXISTS event_log (
  log_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id  INTEGER NOT NULL REFERENCES help_event(event_id),
  ts        REAL NOT NULL,
  action    TEXT NOT NULL,                    -- detect · activate · preempt · wait · alert · confirm · resolve · dismiss · expire · clear · explain · clip
  actor     INTEGER NOT NULL,                 -- 0 system · 3 console · 11~14 student
  detail    TEXT                              -- JSON
);

-- 이벤트 녹화 (이벤트 창 '녹화 영상 다시 보기')
CREATE TABLE IF NOT EXISTS event_clip (
  clip_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id    INTEGER NOT NULL REFERENCES help_event(event_id),
  camera      INTEGER NOT NULL DEFAULT 22,    -- 22 = 팬틸트 cam2
  path        TEXT NOT NULL,                  -- clips/{event_id}.mp4
  started_at  REAL NOT NULL,                  -- 이벤트 확정 시각
  ended_at    REAL,                           -- 완료·오감지·만료 시각
  status      INTEGER NOT NULL DEFAULT 1,     -- 1 녹화 중 · 2 저장됨 · 3 삭제됨
  keep_until  REAL                            -- ended_at + clip_keep_days(기본 7일), 지나면 파일 삭제 후 status=3
);
CREATE INDEX IF NOT EXISTS ix_clip_event ON event_clip(event_id);

-- 장비 연결 이력
CREATE TABLE IF NOT EXISTS device_log (
  log_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  node    INTEGER NOT NULL REFERENCES device(node),
  ts      REAL NOT NULL,
  online  INTEGER NOT NULL,
  note    TEXT
);

-- 반 평균 (10초마다, 개인 값 없음)
CREATE TABLE IF NOT EXISTS class_condition (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                 REAL NOT NULL,
  focus_low_ratio    REAL NOT NULL,
  posture_bad_ratio  REAL NOT NULL,
  n                  INTEGER NOT NULL
);

-- 강사 화면 [설정] (화면에서 바꾸는 값만. 나머지는 control.yaml)
CREATE TABLE IF NOT EXISTS app_setting (
  key         TEXT PRIMARY KEY,               -- alert_sound · rest_ratio · rest_hold_s · clip_keep_days
  value       TEXT NOT NULL,
  updated_at  REAL NOT NULL
);
