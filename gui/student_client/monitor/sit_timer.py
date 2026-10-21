"""장시간 착석 타이머 (SP-228): 앉아 있는 시간이 정해진 시간을 넘으면 스트레칭 권유 신호를 한 번 낸다

- 앉아 있음 = 화면에 사람(자세 또는 얼굴)이 보임 (Monitor.state["present"]). 자세 변화는 보지 않는다.
- 앉아 있는 동안 시간을 세다가 stretch_sec가 되면 "stretch"를 한 번 내고, 계속 앉아 있으면 다시 0부터 센다.
- 사람이 away_sec 넘게 안 보였다가 다시 앉으면 0부터 센다.
  away_sec보다 짧게 안 보이는 것(고개 숙임·가려짐으로 검출이 끊김)은 자리 비움으로 보지 않고 이어서 센다.
"""


class SitTimer:
    def __init__(self, stretch_sec=50 * 60, away_sec=60):
        self.stretch_sec, self.away_sec = stretch_sec, away_sec
        self.reset()

    def reset(self):
        self.start = None          # 이번 착석을 세기 시작한 시각 (None = 앉아 있지 않음)
        self.last_seen = None      # 마지막으로 사람이 보인 시각

    def update(self, present: bool, now: float) -> tuple:
        """매 프레임 호출 → 이번 프레임에 낼 신호 (("stretch",) 또는 ())"""
        if not present:
            if self.last_seen is not None and now - self.last_seen > self.away_sec:
                self.reset()                       # 자리 비움 확정: 다시 앉으면 처음부터
            return ()
        if self.start is None or self.last_seen is None:
            self.start = now
        self.last_seen = now
        if now - self.start >= self.stretch_sec:
            self.start = now                       # 권유 후 다시 처음부터 센다
            return ("stretch",)
        return ()

    def seated_sec(self, now: float) -> float:
        """지금까지 이어서 앉아 있은 시간 (초, 화면 표시용)"""
        return 0.0 if self.start is None else now - self.start