# 고정카메라 손들기·자리비움 단독 시험

전체 연동은 README_SEAT1_INTEGRATION.md를 따른다. 아래는 등록·단독 시험 안내다.

기준 Git: `0040735` (2026-10-21 15:26 KST main). 네트워크·모터·DB 연동을 하지 않는 새 실행기다.
기존 streamer, run_assistant, GUI, protocol, 중앙 EventManager는 수정하지 않았다.

## 1. 압축 적용

이 ZIP은 새 파일만 들어 있는 추가 패키지다. 우선 저장소에서 `git pull --ff-only` 후,
압축의 `service/` 폴더를 저장소 루트에 복사한다. 기존 파일을 덮어쓰도록 만들지 않았다.
예: 저장소가 `~/dev_ws/aiot_repo_3`, ZIP이 Downloads에 있을 때:

```bash
cd ~/dev_ws/aiot_repo_3
git pull --ff-only
unzip ~/Downloads/active_vision_fixed_camera_20261021.zip -d .
```

최신 main이 이후 같은 이름의 파일을 추가했다면 덮어쓰기 전에 비교한다.
이 도구를 실행할 때 같은 카메라를 사용하는 `run_fixed_camera.py`, 기존 vision.py 등을 종료한다.
`--source 0`은 장치 번호이고 논리적 카메라 ID는 항상 cam1이다. 실제 고정카메라 장치가 1이면 `--source 1`.
팬틸트 USB 장치 번호와 혼동하지 않는다.

## 2. 가상환경 확인

기존 YOLO가 실행되던 `ultra` 환경을 사용한다. 프로젝트 전체 requirements.txt를 새로 설치하지 않는다.
환경을 별도로 만든 경우에만 OpenCV·Ultralytics·NumPy·PyYAML 설치가 필요하다.

```bash
source ~/ultra/bin/activate
```

위 경로는 예시다. 기존 ultra 환경의 실제 activate 경로를 사용한다. 이후:

```bash
python -c "import cv2, ultralytics; print(cv2.__version__, ultralytics.__version__)"
python service/jarvis_vision_service/run_fixed_vision.py --help
```

기본 모델은 `yolo11s-pose.pt`. 기존에 가진 가중치는 `--model /실제/경로/yolo11s-pose.pt`로 지정한다.
가중치가 없으면 아래 명령처럼 `--allow-model-download`로 다운로드를 명시적으로 허용한다.
n-pose도 선택할 수 있다. 두 모델을 동시에 실행할 필요는 없다. 일반 detect 모델은 거부한다.
모델 버전·패키지는 시험 중 임의 업그레이드하지 않는다.

## 3. 최초 좌석 등록 (사람마다 매 실행 시 등록하는 것이 아님)

```bash
python service/jarvis_vision_service/run_fixed_vision.py \
  --source 0 --model yolo11s-pose.pt --allow-model-download --calibrate
```

1. 카메라를 고정하고 각 학생이 평소 자세로 앉는다. 앞·뒤 좌석 번호를 실제로 확인한다.
2. 키보드 `1`을 누르고 1번 학생의 **두 어깨 중간 근처**를 클릭한다. 영역을 그리지 않는다.
3. 같은 사람이 평소 자세를 2초간 유지하면 자동 저장된다. 최소 8개 유효 표본이 필요하다.
4. `2`, `3`, `4`도 반복한다. 등록 화면에서는 이벤트를 만들지 않는다.
5. 저장 안내 후 Q 또는 ESC로 종료한다. 창을 클릭해 키보드 포커스를 먼저 준다.

양 어깨가 가려졌거나 측면으로 겹쳐 크기를 측정할 수 없으면 등록을 거부한다.
이 경우 카메라·모니터 배치를 조정하거나 등록 자세를 확인한다. 잘못된 기준으로 강제 등록하지 않는다.
몸을 많이 움직이거나 추적 ID가 끊겨도 다시 클릭해야 한다.
설정은 `service/jarvis_vision_service/config/fixed_seats.local.json`에 저장된다.
1~4 중 등록된 좌석만 실행한다. 빈자리도 포함해 전 좌석을 쓰려면 최초 설정에서 각 좌석을 등록해야 한다.
카메라 이동/회전/렌즈 변경 시 재등록한다. 화면 비율 변경은 자동 거부하지만 위치 변경 자동 검증은 없다.
앞뒤 학생의 기준점이 거의 겹치면 이 방식으로 안정적인 분리가 어려우므로 설치 위치를 바꿔야 한다.

## 4. 자동 매칭·관측 실행

```bash
python service/jarvis_vision_service/run_fixed_vision.py \
  --source 0 --model yolo11s-pose.pt --allow-model-download
```

GPU를 명시하려면 `--device 0`. CPU 시험은 `--device cpu`.
같은 구도에서 촬영한 녹화 파일은 `--source /실제/경로/test.mp4`로 입력한다.
파일은 원래 FPS로 재생하며 느린 분석에서는 최신 프레임 우선으로 일부 프레임을 건너뛴다.
서버·GUI·Arduino가 없어도 실행된다. cam2의 HOME/이동 상태를 요구하지 않는다.

- P=True/False/None: 재실/부재 근거/관측 불가
- H=True/False/None: 손들기/내림/판단 불가
- `MATCH_PENDING`: 좌석 연결 안정화 중 (0.6초)
- `AMBIGUOUS`: 두 사람 또는 두 좌석이 비슷해 연결 보류
- `OCCLUDED_OR_UNMATCHED`: 예상 상체 위치를 사람 박스가 가리지만 연결되지 않음
- `ABSENT_EVIDENCE`: 기준 위치 주변에 사람 근거가 없음 (빈자리의 절대적 증명은 아님)
- `NO_TRACK_ID`: 현재 추적 ID 미확보. 미검출과 구분한다.

노란 원은 저장된 좌석 기준점, 작은 노란 점은 현재 어깨 중심이다.
좌석 기준점은 사람이 움직여도 자동으로 이동하지 않는다. 실제 학생 이름은 식별하지 않는다.
양 어깨가 있어야 새 좌석 매칭을 하며, 박스만 보이면 재실 확정 대신 UNKNOWN으로 보류한다.
이는 사진의 심한 가림에서 잘못된 자리비움 경보를 줄이기 위한 보수적 시작점이다.

## 5. 중앙 판단 시험 (별도 모듈, 중앙 서버에 아직 연결 안 됨)

관측부 `fixed_camera/core.py`는 이벤트를 확정하지 않는다.
실행기가 `decision_demo.py`에 관측을 전달해 중앙 판단을 로컬로 시험한다.

- 질문: 유효 UP 근거 누적 2초. 짧은 UNKNOWN은 누적하지 않고 일시 정지, 0.5초 초과 공백은 후보 초기화.
- 자리비움: 재실이 한 번 확인된 좌석에서 유효 부재 근거 누적 10초. 시작부터 빈자리는 이벤트 없음.
- 복귀: 유효 재실 근거 2초 후 away 해제.
- 모든 확정에 최소 3개 유효 표본. 처리 주기가 0.5초를 넘으면 후보가 초기화될 수 있다.
- 손을 내려도 확정 질문은 남는다. `C`로 현재 질문을 완료한다.
- 완료 뒤 계속 손을 들고 있으면 다시 생성하지 않는다. 손 내림 0.6초 확인 후 재활성화.
- 같은 좌석·같은 종류 중복 병합. 질문 우선, 같은 종류 FIFO. 표시한 큐는 **시험 큐**다.
- 자리비움은 복귀로 종료. 네트워크·모터 명령·LLM·녹화·실제 GUI·질문 만료는 구현 범위 밖.
- 영상 중단은 자리비움이 아님. 재연결 자동화 대신 명확한 오류 후 종료하므로 장치 확인 후 재실행한다.

시간값을 바꾸려면 `--question-hold 2 --away-hold 10 --return-hold 2`.
촬영→분석이 0.8초를 넘은 결과는 UNKNOWN. 느리면 우선 n-pose 모델로 비교한다.

키: `S` 현재 화면 PNG 저장 / `C` 시험 큐 현재 질문 완료 / `Q`, `ESC` 종료.
관측 JSONL·이벤트 JSONL·직접 저장한 화면은 `service/jarvis_vision_service/logs/fixed_camera/`.
관측의 captured_at·processed_at은 Unix 초, **로컬 시험 이벤트의 detected_at은 monotonic 초**이므로
서로 빼서 전체 지연을 계산하면 안 된다. 이벤트 로그 at은 Unix 초다. 이 시험 이벤트 시각을 DB로 보내지 않는다.
관측 로그의 촬영→처리 지연은 processed_at-captured_at으로 확인한다.
실행 세션 UUID로 frame_id 재시작을 구분한다. 로그는 자동 삭제하지 않으므로 시험 후 필요에 따라 정리한다.

실물 패키지 없이 기존 JSONL을 재판정하는 명령:

```bash
python service/jarvis_vision_service/replay_fixed_observations.py /실제/경로/SESSION.observations.jsonl
```

이 명령은 이벤트만 출력하고 프레임을 다시 추론하지 않는다. C 완료 입력은 관측 로그에 없으므로 원래 시험과
큐가 달라질 수 있다. 재생 이벤트 시각은 captured_at(Unix), 실행 중 데모는 monotonic이다.

## 6. 중앙·팬틸트 후속 연동 경계

분석부 입력: 프레임에서 추출한 Person(box, keypoints, track_id, confidence).
출력 `fixed_seat_obs_v1`: camera_id, session, frame_id, captured_at, processed_at, image_size,
seats[{seat,present,hand_up,visible,track_id,match_score,reason}]. match_score는 휴리스틱 점수이며 확률이 아니다.
visible은 이 좌석의 재실 판정 가능 여부다. 개별 손 상태는 별도로 null일 수 있다.

최신 main의 허브는 확정 question/away start/end를 받는다. 아직 seat_obs 중앙 유지시간 판단 경로는 없다.
추후 **중앙에만** 유지시간 판단을 연결한 뒤 EventManager.on_incident에 전달한다.
그때 이 로컬 DecisionDemo를 동시에 실행해 이중 확정하지 않는다. 네트워크 파서를 임의 확장하지 않았다.
좌석 번호는 기존 1..4를 유지하므로 중앙이 기존 프리셋을 선택할 수 있다.
프리셋 없는 좌석도 관측·경보를 버리지 않는다. 프리셋 유무 확인과 추적 오류 처리는 중앙 역할이다.
cam2는 독립 분석기에서 track_target seat/gen을 받아 사람 확인 후 코 좌표를 계산해야 한다.
고정카메라의 track_id·좌표를 cam2에 그대로 사용할 수 없다.
원시 영상 제공/송출은 기존 streamer와 통합하고 **한 VideoCapture의 프레임을 분배**한다.
중앙의 현재 relay(6001/6002)는 GUI 중계만 하므로 분석기 입력에 자동 연결된 것은 아니다.

## 7. 검증 결과와 한계

```bash
python -m unittest discover -s service/jarvis_vision_service/tests -p 'test_fixed*.py' -v
```

로직·모의 실행 시험 17개 통과: 가림/빈자리/중복/두 질문/재활성화/복귀/긴 공백/
저장·비율 검증/좌석→질문→자리비움 파이프라인/CLI help 및 모의 추론 실행기의 관측 로그 저장·종료.
실제 카메라·GPU·YOLO 가중치·모터는 작성 환경에 없어 시험하지 못했다.
사람 박스의 예상 상체 가림 여부는 휴리스틱이며 물체 가림·엎드림·책상 아래로 숙임을 완벽히 알 수 없다.
따라서 실물에서 각 좌석 손들기, 이석·복귀, 몸 숙임, 앞사람 가림, 두 명 동시 손들기를 반드시 구분 시험한다.
실패를 화면과 로그로 확인한 후 기준점 radius·촬영 위치를 조정한다. 낮은 conf만으로 해결하지 않는다.

## 추가 파일

- run_fixed_vision.py: 새 단독 실행기
- fixed_camera/core.py: YOLO 결과를 받는 좌석·손들기 관측
- fixed_camera/config.py: 좌석 기준 저장·검증
- fixed_camera/app.py: 카메라/파일 입력·YOLO·등록·화면·로그
- fixed_camera/decision_demo.py: 별도 로컬 중앙 판단 시험
- replay_fixed_observations.py: 관측 로그 재판정
- tests/test_fixed_camera.py: 로직·연속 흐름 검증
- tests/test_fixed_runner.py: 모의 추론을 사용한 실행기 검증

기존 run_assistant.py의 TODO나 통신을 완성했다고 주장하지 않는다. 제외한 쓰러짐·졸음·집중도는 추가하지 않았다.
