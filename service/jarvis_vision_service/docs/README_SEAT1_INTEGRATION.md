# 1번 좌석 전체 연동 시험 — 2026-10-21

기준 Git: 359966f. 기존 폴더·실행 파일·TCP 바이너리 v3·팬틸트 모듈을 유지했다.
이번 수정은 1번 좌석의 손들기/자리비움과 1인 코 추적 시험용이다.
쓰러짐·졸음·집중도 감지를 추가하지 않았다.

## 변경

- 카메라 -> 중앙 UDP 6001/6002 -> Vision UDP 5021/5022 + 기존 GUI 5011/5012.
- Vision은 `seat_obs`(새 코드 0x25)를 TCP로 전송한다. **중앙과 Vision 모두 이번 common/protocol.py를 적용**해야 한다.
- 중앙 EventManager가 손들기 2초/자리비움 10초/복귀 2초를 확정한다. UNKNOWN은 부재가 아니다.
- 기존 중앙 대기열/GUI/프리셋/Arduino 제어를 사용한다. 영상 분석에 별도 로컬 DecisionDemo를 사용하지 않는다.
- cam2 코 좌표는 `track_obs{gen, frame_id, captured_at, status, cx, cy}`로 보낸다.
  미검출은 LOST와 좌표 없음으로 보낸다. 중앙의 미검출 KeyError도 수정했다.
- cam1/cam2를 각각 별도 모델 인스턴스로 처리한다. 카메라 장치는 streamer만 연다.
- OpenCV 로깅 API를 4.x/5.x 공용 API로 바꿨다.

## 중요한 시험 조건

1. 오늘은 1번 좌석만 이벤트를 전송한다. 등록된 다른 좌석이 있어도 이번 실행기는 제외한다.
2. **cam2에 보이는 사람은 반드시 1번 학생 한 명이어야 한다.** 한 명 보인다는 사실만으로 신원을 증명하는 것은 아니다.
   0명/2명 이상/코 가림이면 LOST. 여러 학생 사이에서 특정 학생을 식별하는 기능은 구현하지 않았다.
3. cam2 촬영~분석이 300ms를 넘으면 추적 좌표를 버린다(기존 모터 규칙).
   FPS/age 로그를 확인하고 우선 yolo11n-pose.pt, --imgsz 416을 권장한다.
4. 서로 다른 PC라면 시스템 시간이 맞아야 한다. 불일치 시 관측·추적이 폐기된다.
5. 이번에는 같은 PC에서 1차 시험 후 분산한다. 포트 5021/5022는 Vision 한 프로세스만 사용한다.
6. 원본 ZIP의 어깨 기준 좌석 매칭을 재사용했다. 70% 박스 겹침 방식으로 바꾸지 않았다.
7. 노트북에서 실제 카메라·YOLO 추론·모터는 직접 시험해야 한다. 자동 시험 통과가 실물 성공을 뜻하지 않는다.

## 0. 패키지 적용

ZIP을 별도 폴더에 풀고 다음 명령을 실행한다. 예시 경로는 실제 압축을 푼 경로에 맞춘다.

```bash
python3 ~/Downloads/active_vision_seat1_integration/apply_update.py --repo ~/dev_ws/aiot_repo_3
python3 ~/Downloads/active_vision_seat1_integration/apply_update.py --repo ~/dev_ws/aiot_repo_3 --apply
```

첫 명령은 확인만 한다. 기존 수정과 충돌하면 파일명과 함께 중지한다. 기존 파일이 같은 내용이면 허용한다.
기존 파일은 저장소의 `.active_vision_backup/`에 백업한다. 이 폴더를 Git에 올리지 않는다.
새 main의 파일 내용이 달라졌다면 덮어쓰지 말고 차이를 확인한다.

이하 **모든 터미널에서** 저장소로 이동하고 기존 ultra 환경을 활성화한다.

```bash
cd ~/dev_ws/aiot_repo_3
# 기존 환경의 실제 경로 사용. 예: source ~/ultra/bin/activate
python -c "import cv2, ultralytics, yaml, serial; print(cv2.__version__, ultralytics.__version__)"
```

기존 GPU 환경을 일괄 재설치하지 않는다. 모델은 기존 pose 가중치의 실제 경로를 사용한다.
가중치가 없다면 좌석 등록 실행기에서 --allow-model-download를 명시해서 받을 수 있다.

## 1. 1번 좌석 등록 (다른 카메라 프로그램은 모두 종료)

```bash
python service/jarvis_vision_service/run_fixed_vision.py \
  --source 0 --model yolo11n-pose.pt --allow-model-download --calibrate
```

- `--source 0`은 고정카메라 장치 번호다. 실제 장치에 맞춘다.
- 학생이 평소 자세로 앉으면 `1`을 누르고 양 어깨 중간을 클릭하고 2초 유지한다.
- 저장 안내를 확인한 뒤 Q 또는 ESC로 종료한다. 등록 후 streamer를 켠다.
- 설정: `service/jarvis_vision_service/config/fixed_seats.local.json`.
- 카메라 위치를 바꾸면 재등록한다. 기존 등록이 유효하면 이 단계는 생략한다.

## 2. Arduino 펌웨어·프리셋 확인

Arduino에는 최신 `hw/camera_controller/camera_controller.ino`를 업로드한다.
배선은 같은 폴더 README를 따른다. 업로드 후 시리얼 모니터를 닫는다.
**run_controller.py는 실행하지 않는다.** 중앙의 PanTiltService가 포트를 직접 연다.
실제 각도는 `service/jarvis_control_service/config/presets.json`이 기준이다.
기존 1번 프리셋(62/86)은 설치 환경에서 확인하고 GUI 시설 관리에서 조정·저장한다.

## 3. 중앙 서버 (터미널 A)

```bash
python service/jarvis_control_service/run_hub.py \
  --host 0.0.0.0 --pantilt-port /dev/ttyACM0 \
  --db service/jarvis_control_service/db/seat1_integration.db
```

Arduino 없이 사전 시험할 때만 --pantilt-port 대신 --pantilt-sim을 사용한다.
새 시험 DB를 사용하므로 기존 열린 질문이 시험에 섞이지 않는다. 다시 실행하면 시험 DB의 열린 사건은 복구된다.
TCP 포트는 config/common.yaml의 control_port(현재 5000)를 사용한다.

## 4. 강사 GUI (터미널 B)

```bash
python gui/instructor_console/run_console.py --host 127.0.0.1
```

실제 서버가 다른 PC면 127.0.0.1 대신 서버 IP. GUI 시설 관리에서 cam2 영상과 1번 프리셋을 확인한다.

## 5. 두 카메라 송출 (터미널 C, D)

```bash
python hw/camera_streamer/run_fixed_camera.py --device 0 --target 127.0.0.1
```

```bash
python hw/camera_streamer/run_pantilt_camera.py --device 1 --target 127.0.0.1
```

능동카메라는 앞서 사용한 1번을 예시로 했다. 장치가 2번이면 --device 2로 바꾼다.
USB 포트 번호와 /dev/videoN 번호는 다를 수 있다. 장치 안내를 확인한다.
같은 카메라를 사용하는 단독 run_fixed_vision.py는 종료되어 있어야 한다.

## 6. Vision 실행 (터미널 E)

```bash
python service/jarvis_vision_service/run_assistant.py \
  --server 127.0.0.1 --model /실제/경로/yolo11n-pose.pt \
  --device 0 --imgsz 416 --record-dir recordings/seat1
```

CPU 시험은 --device cpu. 두 모델을 읽고 예열하므로 시작에 시간이 걸릴 수 있다.
Vision은 웹캠을 열지 않고 중앙에서 받은 영상을 분석한다. GUI가 없어도 영상은 받는다.
로그의 cam2 age가 300ms 미만인지 확인한다. 모델 워밍업 이후에도 크면 추적 성공으로 간주하지 않는다.
모든 실행은 Ctrl+C로 종료한다. UDP 영상이 끊겨도 빈자리로 판정하지 않는다.

녹화는 선택 옵션이다. 지정 시 Vision이 **받아서 분석하는 프레임**을 cam1/cam2 MP4와
촬영 시각 JSONL로 저장한다. 고정 15fps이므로 처리 속도가 느리면 실제 시간보다 빠르게 재생된다.
모든 원본 프레임 보존/중앙 저장/GUI 녹화 재생 기능은 이번 구현에 포함되지 않는다.
영상은 일반 동영상 플레이어로 열 수 있다. 녹화 파일·모델·좌석 설정은 Git에 올리지 않는다.

## 7. 시험 순서와 합격 기준

1. 평소 자세 3초: 오경보 없음.
2. 1번 학생 손들기 약 2초 이상: GUI 질문 생성 -> 중앙 track_target -> 모터 1번 프리셋 이동.
3. cam2에 해당 학생 한 명만 보임: 코를 화면 중심으로 보정. 사람의 코 위치 변화와 모터 방향을 확인.
4. 두 명이 보이거나 코가 가려짐: 잘못된 사람을 선택하지 않고 LOST/기존 재탐색 동작.
5. 손 내림 0.6초: GUI에 손 내림 표시. 질문은 강사가 완료할 때까지 유지.
6. GUI에서 완료: 대기 사건 없으면 HOME 복귀. 계속 손들고 있는 동안 같은 질문 재발생 없음.
7. 처음 재실이 확인된 뒤 자리에서 나와 10초: 자리비움 생성. 복귀 후 2초: 종료.
8. 카메라 가림/송출 중단: UNKNOWN 또는 관측 중단이며 자동 자리비움 생성 없음.

원본의 보수적 매칭 때문에 단순히 몸을 숙이면 UNKNOWN이 될 수 있다. 이 경우 부재 경보가 안 나는 것이 의도다.

## 8. Git에 올리기

```bash
git switch -c integration/seat1-vision-20261021
git status --short
```

ZIP의 CHANGE_LIST.txt에 나온 코드·문서만 올린다. 압축을 푼 경로가 아래 예시와 다르면 수정한다.

```bash
git add --pathspec-from-file="$HOME/Downloads/active_vision_seat1_integration/CHANGE_LIST.txt"
git diff --cached --stat
git commit -m "Integrate seat1 vision observations and pantilt tracking"
git push -u origin integration/seat1-vision-20261021
```

모델·녹화·DB·local.json·백업은 제외한다. 별도 브랜치이므로 main 병합은 팀과 확인 후 진행한다.
수정본을 적용한 뒤 실행해 보고 커밋·push한다. 이 패키지를 만든 작업에서는 원격 Git을 변경하지 않았다.

## 검증

```bash
python -m unittest discover -s service/jarvis_control_service/tools -p 'test_*.py' -v
python -m unittest discover -s service/jarvis_vision_service/tests -p 'test_*.py' -v
```

중앙/프로토콜 48개 + Vision 20개 = 총 68개 통과.
실제 TCP 노드 등록, GUI 미접속 시 UDP Vision 중계, seat_obs 2초 유지 -> track_target,
좌표 없는 LOST 처리까지 모의 Uno와 소켓으로 확인했다. 카메라·GPU·서보 실물은 미검증.
