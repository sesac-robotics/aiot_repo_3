# student_client: 학생 노드 (얼굴·자세 판정)

학생 PC 웹캠으로 **얼굴 상태(졸음·하품·전방 주시)**와 **앉은 자세(앞으로 숙임·고개 기울임·어깨 비대칭)**를 판정하고, 결과를 TCP로 보내는 학생 노드.
요구사항: SR_006 학습 집중 지원, SR_007 바른 자세 유지 지원

> 상태 (2026-10-19)
> - 자세: 학습 모델(수치 MLP + 이미지 CNN 6개) 실시간 적용
> - 얼굴: 실시간은 MediaPipe 규칙 판정. 학습 모델(시계열 + 이미지 CNN) 반영 예정
> - 전송: 바이너리 패킷 v2는 **시험용 임시 형식**. 받는 쪽 프로토콜이 정해지면 변경
> - `run_client.py`(팀 노드)와의 연결은 아직 안 함

## 폴더 구조

| 폴더·파일 | 역할 |
|---|---|
| `run_client.py` | 팀 학생 노드 (control 접속, 쓰러짐 알림, condition_summary) |
| `seat_common/` | 공용 모듈: MediaPipe 특징 추출, 기준 자세 등록, 규칙 판정, 지속 시간 필터, 한글 출력 |
| `monitor/` | 실시간 프로그램 (`monitor_main.py`, `seat_link_main.py`), 자세 앙상블, 전송용 상태 요약 |
| `seat_link/` | 바이너리 패킷(`packet.py`), TCP 송신기(`sender.py`), 시험용 수신기(`test_receiver.py`) |
| `collect/` | 학습 데이터 수집 도구 (자세, 얼굴 + 시선 영역 캘리브레이션) |
| `learning/` | 데이터셋, 모델 학습 (수치 MLP, 이미지 CNN, 시계열) |
| `evaluation/` | 방식 비교, 이벤트 지표, Grad-CAM |
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
| `models/v6/` | 자세 모델 7개 (실시간 필수) | 담당자에게 받기 |
| `models/face/v1/` | 얼굴 모델 (평가·실시간 반영 예정) | 담당자에게 받기 |
| `data/`, `eval/` | 학습·테스트 데이터 (얼굴 이미지) | 학습 담당 PC에만 둠, 공유하지 않음 |

```bash
mkdir -p assets
wget -O assets/pose_landmarker_lite.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
wget -O assets/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

## 실행 (이 폴더에서)

```bash
cd gui/student_client
python -m monitor.monitor_main                 # 실시간 판정 (화면만)
python -m monitor.monitor_main --rule          # 학습 모델 없이 MediaPipe 규칙만
```

바이너리 전송 시험 (터미널 2개):
```bash
python -m seat_link.test_receiver --port 6000                                       # 1. 수신기
python -m monitor.seat_link_main --server 127.0.0.1 --port 6000 --seat 1 --interval 2  # 2. 판정 + 전송
```

- 시작하면 **바른 자세 10초** 기준 등록 (허리를 펴고 화면 정면)
- 키: `c` 기준 다시 등록, `ESC` 종료
- 판정은 지속 시간 필터로 확정: 졸음 1.5초, 하품 1초, 전방 주시 미흡 2초, 자세 3초

## 바이너리 패킷 v2 (시험용, 14바이트, 빅엔디언)

| 바이트 | 필드 | 값 |
|---|---|---|
| 0–1 | 시작 표시 | `0xAA 0x55` |
| 2 | 버전 | `0x02` |
| 3 | 메시지 종류 | `0x10` 상태 요약 |
| 4 | 좌석 | 1~255 |
| 5 | drowsy (졸음) | 0/1 |
| 6 | yawn (하품) | 0/1 |
| 7 | not_forward (전방 주시 미흡) | 0/1 |
| 8 | forward_lean (앞으로 숙임) | 0/1 |
| 9–12 | 시각 | 보낸 시각 (초, uint32) |
| 13 | 체크섬 | 2~12번 바이트 XOR |

- 전송 주기(`--interval`, 기본 10초) 동안 **한 번이라도 확정**되면 1
- 기준 등록 중이거나 사람이 없으면 모두 0
- 예: 좌석 1, 졸음 + 앞으로 숙임 → `AA 55 02 10 01 01 00 00 01 6A D5 DA 80 F6`
- 형식 변경은 `seat_link/packet.py`만 고치면 됨 (판정 코드는 dict만 넘김)

## 성능 (균형 정확도, MediaPipe 규칙만 → 학습 모델)

| 영역 | 평가 | 규칙만 | 학습 모델 |
|---|---|---|---|
| 자세 | 처음 보는 장소·옷 테스트 1회 | 0.502 | 0.885 |
| 얼굴 | 검증 세션 (최종 테스트 전) | 0.526 | 0.885 (오경보 32% → 2.8%) |

## 주의

- `data/`, `eval/`에는 **얼굴 이미지(생체 정보)**가 있다. 이 폴더의 `.gitignore`가 `data/`, `eval/`, `models/`, `assets/`, `*.task`, `*.pt`를 제외하므로 `git add .` 대신 파일 이름을 지정해서 add 한다
- 판정 결과 알림은 학생 본인 화면에만, 강사에게는 반 전체 상태만 전달하는 것이 원칙 (SR_006)
