"""좌석별 판정 요약: SeatMatcher 결과를 일정 시간 모아 한 줄로 만든다 (실물 시험에서 원인 찾기용).

4석 시험에서 "일부 좌석이 계속 모름"일 때, 그 이유(NO_TRACK_ID·OCCLUDED·AMBIGUOUS …)가
기록에 남지 않아 원인을 찾을 수 없었다. run_assistant 가 10초마다 이 줄을 로그로 남긴다.

예) 1번 있음 92%(손 4%) | 2번 있음 85% 모름 15%(NO_TRACK_ID) | 3번 모름 70%(OCCLUDED_OR_UNMATCHED) | 4번 빔 100%
"""
from collections import Counter


class SeatReport:
    def __init__(self, every_s=10.):
        self.every_s = every_s
        self.started = None
        self.counts: dict[int, Counter] = {}

    def add(self, rows, now):
        """rows = SeatMatcher.observe() 결과. every_s 가 지났으면 요약 한 줄을 돌려주고 다시 센다 (아니면 None)."""
        if self.started is None:
            self.started = now
        for r in rows:
            c = self.counts.setdefault(r['seat'], Counter())
            c['frames'] += 1
            if r['present'] is True:
                c['present'] += 1
                c['hand'] += r.get('hand_up') is True
            elif r['present'] is False:
                c['absent'] += 1
            else:
                c['unknown'] += 1
                c['why:' + str(r.get('reason'))] += 1
        if now - self.started < self.every_s:
            return None
        line = ' | '.join(self._seat(s, self.counts[s]) for s in sorted(self.counts))
        self.started, self.counts = now, {}
        return line

    @staticmethod
    def _seat(seat, c):
        n = c['frames'] or 1
        pct = lambda k: round(100 * c[k] / n)                      # noqa: E731
        parts = []
        if c['present']:
            parts.append(f"있음 {pct('present')}%" + (f"(손 {pct('hand')}%)" if c['hand'] else ''))
        if c['absent']:
            parts.append(f"빔 {pct('absent')}%")
        if c['unknown']:
            why = max((k for k in c if k.startswith('why:')), key=lambda k: c[k])[4:]
            parts.append(f"모름 {pct('unknown')}%({why})")
        return f"{seat}번 " + ' '.join(parts)
