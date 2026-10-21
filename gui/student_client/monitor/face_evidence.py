"""얼굴 판정 근거 보정: MediaPipe가 직접 잰 눈 감김·입 벌림 값으로 학습 모델의 졸음·하품 판정을 확인한다

학습 모델은 고개 각도·안경 같은 다른 단서로 졸음·하품을 판정하는 경우가 있다 (10-21 실시간 확인).
  - 졸음: 최근 눈 감김 평균이 eye_min 미만이면 졸음 확률을 0으로 하고 나머지 상태로 다시 나눈다
  - 하품: 최근 jaw_sec초 동안의 최대 입 벌림이 jaw_min 미만이면 하품 확률을 0으로 하고 나머지 상태로 다시 나눈다
    (하품을 반복하는 사이 입이 잠깐 닫히는 순간은 하품으로 유지, 입을 계속 다문 상태만 제외)
  - 눈 감김 지속: 눈 감김이 closed_eye 이상으로 closed_sec 넘게 이어지면 모델과 관계없이 졸음
기준값 근거 (학습 데이터): 졸음 프레임의 83~94%가 눈 감김 0.35 이상, 약한 하품 입 벌림 평균 0.26.
꾸벅임(눈을 뜬 채 고개만 떨굼)은 졸음으로 보지 않게 된다 (자세 판정의 앞으로 숙임으로 처리).
"""
import numpy as np

from collect.face_guide import FACE_CLASSES
from learning.face_dataset import NUM_INPUTS

COL = {n: i for i, n in enumerate(NUM_INPUTS)}
EYES = (COL["eyeBlinkLeft"], COL["eyeBlinkRight"])
JAW = COL["jawOpen"]
DROWSY, YAWN = FACE_CLASSES.index("drowsy"), FACE_CLASSES.index("yawn")


class EvidenceGate:
    def __init__(self, eye_min=0.35, jaw_min=0.15, closed_eye=0.5, closed_sec=2.0, eye_frames=5, jaw_sec=1.0):
        self.eye_min, self.jaw_min, self.jaw_sec = eye_min, jaw_min, jaw_sec
        self.closed_eye, self.closed_sec, self.eye_frames = closed_eye, closed_sec, eye_frames
        self.reset()

    def reset(self):
        self.eyes = []              # 최근 눈 감김 (깜빡임 한 프레임에 흔들리지 않게 평균)
        self.jaws = []              # 최근 jaw_sec초의 (시각, 입 벌림)
        self.closed_since = None
        self.note = ""              # 화면 확인용: 이번 프레임에 한 보정

    def describe(self):
        return (f"근거 보정 (졸음: 눈 감김 {self.eye_min:g} 이상, 하품: 최근 {self.jaw_sec:g}초 입 벌림 {self.jaw_min:g} 이상, "
                f"눈 감김 {self.closed_eye:g} 이상 {self.closed_sec:g}초 → 졸음)")

    def apply(self, p, x, now):
        """모델 확률 p (5,) + 이번 프레임 수치 x (56,) → 보정한 확률"""
        eye = float(np.mean([x[i] for i in EYES]))
        self.jaws = [(t, j) for t, j in self.jaws if now - t <= self.jaw_sec] + [(now, float(x[JAW]))]
        jaw = max(j for _, j in self.jaws)
        self.eyes = (self.eyes + [eye])[-self.eye_frames:]
        eye_avg = float(np.mean(self.eyes))
        if eye >= self.closed_eye:
            self.closed_since = now if self.closed_since is None else self.closed_since
        else:
            self.closed_since = None
        if self.closed_since is not None and now - self.closed_since >= self.closed_sec:
            self.note = f"눈 감김 {now - self.closed_since:.1f}초 → 졸음"
            out = np.zeros_like(p)
            out[DROWSY] = 1.0
            return out
        out, notes = p.copy(), []
        top = int(p.argmax())
        if eye_avg < self.eye_min:
            out[DROWSY] = 0.0
            if top == DROWSY:
                notes.append(f"졸음 제외 (눈 감김 {eye_avg:.2f})")
        if jaw < self.jaw_min:
            out[YAWN] = 0.0
            if top == YAWN:
                notes.append(f"하품 제외 (입 벌림 {jaw:.2f})")
        self.note = ", ".join(notes)
        s = out.sum()
        return out / s if s > 0 else p