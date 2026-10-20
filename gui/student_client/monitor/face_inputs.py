"""실시간 얼굴 입력: Face Landmarker 결과 + 영역 기준값 → 학습 때와 같은 수치 56개, 얼굴 crop 이미지

학습 데이터(collect.face_recorder → learning.face_dataset)와 같은 계산을 그대로 따른다.
  - 수치 56개 = blendshape 51개(_neutral 제외) + 화면 대비 yaw·pitch, roll, 앞쪽 구간 밖 거리, 책상 대비 pitch
  - 각도는 CSV처럼 소수 둘째 자리, blendshape는 넷째 자리로 반올림
  - 얼굴 crop = 랜드마크 상자 × 1.4 정사각형 → 128×128, 수집 때처럼 JPEG(품질 90)를 한 번 거친 뒤 RGB로 변환
영역 기준값 형식: {"screen": {"yaw", "pitch"}, "edge_a": …, "edge_b": …, "desk": …} (edge_a·b는 없어도 됨)
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from collect.face_recorder import crop_face, head_roll
from learning.face_dataset import BS_INPUTS, NUM_INPUTS, _front_range
from seat_common.face_features import head_angles

JPEG_QUALITY = 90       # collect.face_recorder 저장 품질과 같게


def load_zones(path):
    """영역 JSON → zones dict. 수집 세션 파일({"zones": {...}})과 zones만 있는 파일 모두 허용"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    zones = data.get("zones", data)
    if "screen" not in zones:
        raise ValueError(f"{path}: 영역에 'screen'이 없습니다")
    return zones


def angle_inputs(yaw, pitch, roll, zones):
    """각도 5개 (learning.face_dataset._session_rows와 같은 순서·계산)"""
    scr, desk = zones["screen"], zones.get("desk", zones["screen"])
    lo, hi = _front_range(zones)
    return [yaw - scr["yaw"], pitch - scr["pitch"], roll,
            max(lo - yaw, yaw - hi, 0.0), pitch - desk["pitch"]]


def numeric_inputs(result, zones):
    """검출 결과 → (56,) float32, 얼굴·blendshape·변환 행렬이 없으면 None"""
    if result is None or not result.face_blendshapes or not result.facial_transformation_matrixes:
        return None
    matrix = result.facial_transformation_matrixes[0]
    yaw, pitch = head_angles(matrix)
    yaw, pitch, roll = round(yaw, 2), round(pitch, 2), round(head_roll(matrix), 2)
    scores = {c.category_name: c.score for c in result.face_blendshapes[0]}
    bs = [round(scores.get(n, 0.0), 4) for n in BS_INPUTS]
    x = np.array(bs + angle_inputs(yaw, pitch, roll, zones), dtype=np.float32)
    assert len(x) == len(NUM_INPUTS)
    return x


def face_image(frame_bgr, result):
    """원본 프레임(점 그리기 전) + 검출 결과 → 얼굴 crop PIL RGB 이미지, 얼굴이 너무 작으면 None"""
    if result is None or not result.face_landmarks:
        return None
    face = crop_face(frame_bgr, result.face_landmarks[0])
    if face is None:
        return None
    ok, buf = cv2.imencode(".jpg", face, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if ok:
        face = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return Image.fromarray(cv2.cvtColor(face, cv2.COLOR_BGR2RGB))