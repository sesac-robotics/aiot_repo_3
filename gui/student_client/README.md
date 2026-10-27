# student_client: 학생 노드 (얼굴·자세 판정 + 학생 화면)

학생 PC 웹캠으로 **얼굴 상태(졸음·하품·전방 주시)**와 **앉은 자세(앞으로 숙임·고개 기울임·어깨 비대칭)**를 판정해
본인 화면에 알림을 띄우고, 상태 요약을 control 서버로 보내는 학생 노드 (`student_client_1` ~ `student_client_4`).
요구사항: SR_006 학습 집중 지원, SR_007 바른 자세 유지 지원

> 상태 (2026-10-22)
> - 얼굴: 학습 모델 (시간 흐름 3개 + 이미지 2개, 근거 보정) 실시간 적용
> - 자세: 학습 모델 (수치 MLP + 이미지 6개) 실시간 적용
> - 학생 화면(PyQt6): 자리 등록, 상태 카드, 본인 알림, 쓰러짐 확인 창
> - 서버 전송: 팀 공통 바이너리 프레임 v3, `condition_summary` 10초마다 (묶음 2개 + 개별 6개)

## 폴더 구조

| 폴더·파일 | 역할 |
|---|---|
| `student_gui/` | **학생 화면 (실사용)**. `python -m student_gui.app` |
| `run_client.py` | 터미널용 학생 노드: 웹캠 판정 + 전송 (`monitor/seat_link_main.py`). `--no-camera`면 연결만 시험 |
| `monitor/` | 판정 (`monitor_main.py` 개발 화면, 얼굴·자세 앙상블, 좌석 저장, 전송 값 계산 `condition.py`) |
| `seat_link/control_link.py` | control 연결 (`run_client.py`, `seat_link_main.py`용) |
| `seat_common/` | 공용 모듈: MediaPipe 특징 추출, 규칙 판정, 지속 시간 필터, 한글 출력 |
| `collect/`, `learning/`, `evaluation/` | 데이터 수집, 모델 학습, 방식 비교·이벤트 지표 |
| `mediapipe_only/` | MediaPipe 규칙만 쓴 기준선 (비교용 보관) |
| `results/face_history.md` | 얼굴 판정 성능 히스토리 |

`common/`(팀)과 이름이 겹치지 않도록 이 폴더의 공용 모듈은 `seat_common/`을 쓴다.

## 준비

### 1. 패키지 (저장소 최상위에서)
```bash
python3 -m venv ~/venv/aiot_repo_3
source ~/venv/aiot_repo_3/bin/activate
pip install -r requirements.txt -c constraints.txt
```
- NVIDIA 드라이버가 CUDA 13 이상 지원 필요 (`nvidia-smi`)
- `sounddevice` 오류 시 `sudo apt install -y libportaudio2`

### 2. git에 없는 파일 (이 폴더 아래에 둔다)
| 위치 | 내용 | 받는 방법 |
|---|---|---|
| `assets/pose_landmarker_lite.task`, `assets/face_landmarker.task` | MediaPipe 모델 | 아래 명령 |
| `models/v6/` | 자세 모델 7개 (`posture_mlp.pt`, `cnn_cropmask_reg{,_s1,_s2}.pt`, `cnn_sil{,_s1,_s2}.pt`) | 담당자에게 받기 (USB) |
| `models/face/v2/` | 얼굴 이미지 모델 2개 (`cnn_ls_erase{,_s1}.pt`) | 담당자에게 받기 (USB) |
| `models/face/v1/` | 얼굴 시간 흐름 모델 3개 (`seq{,_s1,_s2}.pt`) | 담당자에게 받기 (USB) |
| `data/`, `eval/` | 학습·테스트 데이터 (얼굴 이미지) | 학습 담당 PC에만 둠, 공유하지 않음 |

```bash
mkdir -p assets
wget -O assets/pose_landmarker_lite.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
wget -O assets/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```
- 모델 파일이 없으면 `--rule --face-rule`로 MediaPipe 규칙만 써서 실행할 수 있다 (`assets/`는 필요)

### 3. control 주소
- 저장소 최상위 `config/common.yaml`의 `control_host`(관제 노트북 IP), `control_port`(5000)
- 실행할 때 `--server IP`, `--port N`을 주면 그 값이 우선

## 학생 화면 실행 (이 폴더에서)

```bash
cd gui/student_client
python -m student_gui.app --seat 1                      # 서버 주소는 config/common.yaml
python -m student_gui.app --seat 1 --server 127.0.0.1   # 같은 PC에서 시험
python -m student_gui.app --seat 1 --stretch-min 1      # 스트레칭 알림 시험 (1분)
```
- 좌석은 1~4. 같은 좌석으로 두 개를 동시에 띄우지 않는다 (control이 먼저 붙은 쪽을 끊음)
- 처음 실행하면 **[자리 등록]**: 이름 → 바른 자세 10초 → 화면 방향 측정. 값은 `data/seats/`에 저장되어 다음 실행 때 불러온다
- 평소 화면: 웹캠, 집중·자세 상태 카드, [자리 등록](다시 등록), [알림 끄기]
- `monitor.monitor_main` 옵션(`--source`, `--rule`, `--face-rule`, `--img-every` ...)도 그대로 쓸 수 있다

### 판정 확정 시간
| 항목 | 확정 | 해제 |
|---|---|---|
| 졸음 | 1.5초 | 1초 |
| 하품 | 1초 | 1초 |
| 전방 주시 미흡 | 2초 | 1초 |
| 자세 3종 | 학생 화면 30초 (개발 화면 3초) | 2초 |

### 본인 알림 (`student_gui/messages.py` 규칙 표)
- 확정된 순간에만 띄운다. 겹치면 우선순위가 가장 높은 하나만 표시
  - 졸음 > 스트레칭 > 전방 주시 미흡 > 하품 > 앞으로 숙임 > 고개 기울임 > 어깨 비대칭
- 다시 알리기 간격 (종류별, 실제로 띄운 순간부터): 졸음·하품·자세 120초, 전방 주시 미흡·스트레칭 60초
- 장시간 착석: 앉아 있는 시간이 `--stretch-min`(기본 50분)마다 스트레칭 권유. 1분(`--away-reset-sec`) 넘게 비우면 0부터
- [알림 끄기]는 본인 알림만 끈다. 쓰러짐 확인 창은 항상 뜬다

### 개발 화면 (판정 확인용)
```bash
python -m monitor.monitor_main --seat 1         # 실시간 판정 (화면만, 자세 3초)
```
- 키: `c` 기준 다시 등록, `ESC` 종료

## 서버 연동

### 주고받는 메시지 (`common/protocol.py`, 바이너리 프레임 v3, 포트 5000)
| 방향 | type | value | 언제 |
|---|---|---|---|
| 학생 → control | `condition_summary` | 아래 8개 bool | 10초마다 |
| 학생 → control | `heartbeat` | 카메라 상태(`cam_ok`), fps 등 | 2초마다 |
| 학생 → control | `fall_confirm` | `target_seat` | [확인했어요] 누름 |
| control → 학생 | `fall_alert` | `target_seat`, `stage` (neighbor / all) | 쓰러짐 미확인 15초 옆자리, 30초 전체 |
| control → 학생 | `fall_alert_cancel` | | 쓰러짐 확인·해제 |

`condition_summary` value
```
focus_low, posture_bad                    # 묶음: 얼굴 3개 / 자세 3개 중 하나라도 true (강사 화면 반 평균용)
drowsy, yawn, not_forward                 # 얼굴
forward_lean, head_tilt, shoulder_tilt    # 자세 (규칙 모드의 거북목·구부정은 forward_lean)
```
- 지난 10초 동안 **한 번이라도 확정**되었으면 true. 자리 비움·자리 등록 중에는 모두 false
- 계산은 `monitor/condition.py` 하나 (학생 화면과 `run_client.py`가 같이 씀)
- 값 검사: 묶음 2개는 필수 bool, 개별 6개는 있으면 bool (`CONDITION_DETAIL_KEYS`). 틀리면 control이 버리고 `수신 실패` 로그
- 끊기면 2초마다 자동 재접속. 끊긴 동안 `condition_summary`는 버리고 `fall_confirm`은 모아 두었다 보냄
- 반영 지연: 학생 10초 묶음 + 서버 10초 계산 → 강사 화면에는 약 10~20초 뒤

### 연동 시험 1: 같은 PC (터미널 여러 개, 저장소 최상위 기준)
```bash
# 1. control 서버
cd service/jarvis_control_service && python3 run_hub.py
# 2. 학생 화면
cd gui/student_client && python -m student_gui.app --seat 1 --server 127.0.0.1
# 3. (선택) 강사 화면 - 반 평균 확인
python3 gui/instructor_console/run_console.py
```
control 쪽 확인 (`service/jarvis_control_service`에서)
```bash
grep hub.student logs/hub.log | tail -5
```
- `수신 OK student_client_1 condition_summary seat=1 focus_low=... posture_bad=...` 가 10초마다 찍히면 성공
- 1분마다 `학생 수신 현황 | ...` 한 줄 요약. `condition_summary 가 ... 초째 없음` 경고가 나오면 학생 화면 판정 확인

### 연동 시험 2: 카메라 없이 개별 항목 고정 전송
```bash
python3 run_client.py --seat 1 --server 127.0.0.1 --no-camera --on drowsy head_tilt
```
- `--on`에 준 항목만 true로 10초마다 보낸다 (묶음 값은 자동 계산). 터미널에서 Enter = `fall_confirm`

### 연동 시험 3: 개별 8개 키 확인 (시험 수신기)
control 로그는 묶음 2개만 찍으므로, 개별 값은 시험 수신기로 본다. `service/jarvis_control_service`에서:
```bash
python3 - <<'EOF'
import socket, hub
from common.protocol import FrameReader, parse_message
srv = socket.create_server(("0.0.0.0", 5001)); print("시험 수신기 :5001 대기")
conn, addr = srv.accept(); print("접속:", addr); r = FrameReader()
while (data := conn.recv(4096)):
    for f in r.feed(data):
        m = parse_message(f)
        if m["type"] == "condition_summary":
            v = m["value"]; on = [k for k, x in v.items() if x]
            print(f"{m['node']} seq={m['seq']} true: {on or '없음'}  ({len(v)}개 키)")
EOF
```
학생 쪽은 `--port 5001`로 실행 (`python -m student_gui.app --seat 1 --server 127.0.0.1 --port 5001`).
해당 상태를 연출하면 그 항목 + 묶음이 true로 찍힌다.

### 연동 시험 4: 쓰러짐 알림 왕복
control을 켜고 학생 화면(1번 좌석)을 띄운 뒤, `service/jarvis_control_service`에서:
```bash
python3 - <<'EOF'
import time, hub
from common.node_client import NodeClient
c = NodeClient("vision_service", "127.0.0.1", 5000, on_message=lambda m: None)
c.start(); time.sleep(1)
c.send("fall", seat=2, value={"state": "start"}, conf=0.95)
print("2번 좌석 쓰러짐 보냄"); time.sleep(40); c.stop()
EOF
```
- 15초 뒤 1번 학생 화면에 "옆자리 확인 요청" 창 + 알림음 → [확인했어요] → control 로그 `수신 OK student_client_1 fall_confirm`
- 시험 뒤 정리: 서버를 끄고 `rm -f db/hub.db db/hub.db-wal db/hub.db-shm` (남은 시험 이벤트가 재시작 때 복구됨)

### 다른 PC에서 접속 (학생 노트북)
- `config/common.yaml`의 `control_host`를 관제 노트북 IP로 (또는 `--server IP`)
- 관제 노트북 방화벽에서 TCP 5000 허용, 같은 네트워크인지 확인 (`ping IP`)

## 성능 (균형 정확도)

| 영역 | 평가 | MediaPipe 규칙만 | 학습 모델 |
|---|---|---|---|
| 자세 | 처음 보는 테스트 4세션 평균 | 0.591 | 0.872 |
| 얼굴 | 처음 보는 장소 테스트 (실시간 재생) | 0.540 | 0.787 (정상 10분당 오경보 4.4건, 규칙만 48.5건) |

- 모델 선정·학습 방식 비교·문제와 해결·성능 추이: [docs/experiments.md](docs/experiments.md)
- 얼굴 단계별 성능 히스토리: [results/face_history.md](results/face_history.md)

## 주의

- `data/`, `eval/`에는 **얼굴 이미지(생체 정보)**가 있다. 이 폴더의 `.gitignore`가 `data/`, `eval/`, `models/`, `assets/`, `*.task`, `*.pt`를 제외하므로 `git add .` 대신 파일 이름을 지정해서 add 한다
- 판정 결과 알림은 학생 본인 화면에만, 강사에게는 반 전체 상태만 전달하는 것이 원칙 (SR_006)
- `seat_link/packet.py`, `sender.py`, `test_receiver.py`는 폐기된 시험 형식(패킷 v2)이다. control은 받지 않는다