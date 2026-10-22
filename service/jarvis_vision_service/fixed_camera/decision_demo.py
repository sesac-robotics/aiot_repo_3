"""Local central-decision simulator. NOT part of the vision detector.

No network or motor writes. A future Control adapter may reuse this evidence
logic, but must call the real EventManager in one place, not run two managers.
"""

class Evidence:
    def __init__(self, gap=.5):
        self.gap = gap
        self.reset()

    def reset(self):
        self.total = 0.
        self.last = None
        self.previous = False
        self.count = 0
        self.unknown_since = None

    def update(self, value, now, hold):
        if self.last is not None and now-self.last > self.gap:
            self.reset()
        if value is None:
            if self.unknown_since is None:
                self.unknown_since = now
            elif now-self.unknown_since > self.gap:
                self.reset()
                self.unknown_since = now
        elif self.unknown_since is not None:
            if now-self.unknown_since > self.gap:
                self.reset()
            self.unknown_since = None
        if value is False:
            self.reset()
        elif value is True:
            if self.previous and self.last is not None:
                self.total += max(0., now-self.last)
            self.count += 1
        self.previous = value is True
        self.last = now
        return self.count >= 3 and self.total+1e-9 >= hold


class DecisionDemo:
    def __init__(self, question_s=2., away_s=10., return_s=2., gap=.5):
        self.question_s, self.away_s, self.return_s = question_s, away_s, return_s
        self.gap = gap
        self.states = {}
        self.events = {}
        self.next_id = 1
        self.last_frame = {}
        self.last_time = None
        self.question_armed = {}

    def process(self, obs, now):
        key = (obs['session'], obs['camera_id'])
        if self.last_time is not None and now <= self.last_time:
            return []
        if obs['frame_id'] <= self.last_frame.get(key, -1):
            return []
        self.last_frame[key] = obs['frame_id']
        self.last_time = now
        changes = []
        for row in obs['seats']:
            seat = row['seat']
            s = self.states.setdefault(seat, {'seen': False, **{
                k: Evidence(self.gap) for k in ('up', 'down', 'away', 'back')}})
            p, h = row['present'], row['hand_up']
            if p is True:
                s['seen'] = True
            if s['up'].update(h if p is True else None, now, self.question_s):
                if self.question_armed.get(seat, True) and (seat, 'question') not in self.events:
                    changes.append(self._start(seat, 'question', now))
                    self.question_armed[seat] = False
            if s['down'].update(None if h is None else not h, now, .6):
                self.question_armed[seat] = True
            absent = None if p is None else not p
            if s['away'].update(absent if s['seen'] else None, now, self.away_s):
                if (seat, 'away') not in self.events:
                    changes.append(self._start(seat, 'away', now))
            if s['back'].update(p, now, self.return_s):
                if (seat, 'away') in self.events:
                    changes.append(self.close(seat, 'away', now, 'returned'))
            question = self.events.get((seat, 'question'))
            if question and h is not None:
                question['hand_down'] = not h
        return changes

    def _start(self, seat, kind, now):
        event = {'event_id': self.next_id, 'seat': seat, 'kind': kind,
                 'detected_at': now, 'hand_down': False}
        self.next_id += 1
        self.events[seat, kind] = event
        return {'action': 'start', **event}

    def close(self, seat, kind, now, reason='resolved'):
        event = self.events.pop((seat, kind))
        if kind == 'question':
            self.question_armed[seat] = False
            if seat in self.states:
                self.states[seat]['down'].reset()
                self.states[seat]['up'].reset()
        return {'action': 'close', 'reason': reason, 'closed_at': now, **event}

    def queue(self):
        return sorted(self.events.values(), key=lambda e: (
            2 if e['kind'] == 'question' else 3, e['detected_at'], e['event_id']))
