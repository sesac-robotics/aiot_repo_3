"""Dependency-free pose rules and conservative one-to-one seat association.

Coordinates are normalized to the delivered, unmirrored image. A missing match
is NOT absence evidence when another body covers the expected upper body.
This is a geometric heuristic; it cannot prove that an occluded seat is empty.
"""
from dataclasses import dataclass
from functools import lru_cache
import math


def valid_point(k, i, threshold=.5):
    return (len(k) > i and len(k[i]) >= 3
            and all(math.isfinite(float(v)) for v in k[i][:3])
            and k[i][2] >= threshold and 0 < k[i][0] < 1 and 0 < k[i][1] < 1)


@dataclass
class Person:
    box: tuple
    keypoints: list
    track_id: int | None
    confidence: float

    def anchor(self, threshold=.5):
        k = self.keypoints
        if all(valid_point(k, i, threshold) for i in (5, 6)):
            return ((k[5][0]+k[6][0])/2, (k[5][1]+k[6][1])/2)
        return None

    def scale(self, aspect, threshold=.5):
        k = self.keypoints
        if self.anchor(threshold) is None:
            return None
        return math.hypot((k[5][0]-k[6][0])*aspect, k[5][1]-k[6][1])


def hand_state(person, aspect, threshold=.5, rise=.20):
    """At least one reliably UP arm is enough; DOWN needs both arms.

    If shoulders overlap in profile, use a visible same-side torso as scale.
    Missing/degenerate scale yields UNKNOWN rather than a guessed boolean.
    """
    k = person.keypoints
    lengths = []
    def distance(a, b):
        return math.hypot((k[a][0]-k[b][0])*aspect, k[a][1]-k[b][1])
    if all(valid_point(k, i, threshold) for i in (5, 6)):
        lengths.append(distance(5, 6))
    for a, b in ((5, 11), (6, 12)):
        if all(valid_point(k, i, threshold) for i in (a, b)):
            lengths.append(.7*distance(a, b))
    scale = max(lengths, default=0)
    if scale < .015:
        return None, 'NO_BODY_SCALE'
    states = []
    for s, w in ((5, 9), (6, 10)):
        if not all(valid_point(k, i, threshold) for i in (s, w)):
            states.append(None)
        else:
            height = (k[s][1]-k[w][1])/scale
            states.append(True if height > rise else False if height < rise*.5 else None)
    if True in states:
        return True, 'UP'
    if all(x is False for x in states):
        return False, 'DOWN'
    return None, 'ARM_UNKNOWN'


def distance(a, b, aspect):
    return math.hypot((a[0]-b[0])*aspect, a[1]-b[1])


def min_assignment(costs):
    """Exact minimum-cost assignment with unmatched columns; <=4 seat rows."""
    @lru_cache(None)
    def solve(row, used):
        if row == len(costs):
            return 0., ()
        tail, ids = solve(row+1, used)
        best = (2.0+tail, (None,)+ids)
        for col, cost in enumerate(costs[row]):
            if cost is None or used & (1 << col):
                continue
            tail, ids = solve(row+1, used | (1 << col))
            candidate = (cost+tail, (col,)+ids)
            if candidate[0] < best[0]:
                best = candidate
        return best
    return solve(0, 0)[1]


class SeatMatcher:
    def __init__(self, config, kpt_conf=.5, confirm_s=.6, gap_s=.5):
        self.config = config
        self.aspect = config['aspect']
        self.seats = {int(s): v for s, v in config['seats'].items()}
        self.kpt_conf, self.confirm_s, self.gap_s = kpt_conf, confirm_s, gap_s
        self.links = {}

    def reset(self):
        self.links.clear()

    def observe(self, people, now):
        # Prevent uncontrolled combinatorial work on false detections.
        if len(people) > 12:
            return self.unknown('TOO_MANY_PEOPLE')
        seats = sorted(self.seats)
        anchors = [p.anchor(self.kpt_conf) for p in people]
        costs = []
        for seat in seats:
            cfg = self.seats[seat]
            row = []
            for i, p in enumerate(people):
                a = anchors[i]
                scale = p.scale(self.aspect, self.kpt_conf)
                if a is None or scale is None:
                    row.append(None)
                    continue
                d = distance(a, cfg['anchor'], self.aspect)/cfg['radius']
                ratio = scale/cfg['scale']
                if d > 1 or not .45 <= ratio <= 2.2:
                    row.append(None)
                    continue
                cost = d+.25*abs(math.log(ratio))
                old = self.links.get(seat)
                if (p.track_id is not None and old and old['id'] == p.track_id
                        and now-old['last'] <= self.gap_s):
                    cost = max(0, cost-.15)
                row.append(cost)
            costs.append(row)
        choices = min_assignment(costs)
        result = []
        for r, seat in enumerate(seats):
            cfg = self.seats[seat]
            candidates = sorted((c, i) for i, c in enumerate(costs[r]) if c is not None)
            i = choices[r]
            reason = 'NO_MATCH'
            present = hand = None
            tid = None
            matching = 0.
            if i is not None:
                cost = costs[r][i]
                # Reject ambiguity on both the seat and the person side.
                rivals = [c for j, c in enumerate(costs[r]) if j != i and c is not None]
                other_seats = [row[i] for rr, row in enumerate(costs) if rr != r and row[i] is not None]
                ambiguous = any(c-cost < .18 for c in rivals+other_seats)
                if ambiguous:
                    reason = 'AMBIGUOUS'
                    self.links.pop(seat, None)
                else:
                    p = people[i]
                    tid = p.track_id
                    old = self.links.get(seat)
                    # No tracker ID is a valid conservative UNKNOWN, not vacancy.
                    if tid is None:
                        self.links.pop(seat, None)
                        reason = 'NO_TRACK_ID'
                    else:
                        if old is None or old['id'] != tid or now-old['last'] > self.gap_s:
                            old = {'id': tid, 'since': now, 'last': now}
                        old['last'] = now
                        self.links[seat] = old
                        matching = max(0., 1-cost/2)
                        if now-old['since'] >= self.confirm_s:
                            present = True
                            hand, reason = hand_state(p, self.aspect, self.kpt_conf)
                        else:
                            reason = 'MATCH_PENDING'
            else:
                # Expected head/upper torso window, derived from saved shoulder size.
                # Any person box crossing it can conceal the seat, regardless of ID.
                x, y = cfg['anchor']
                sx = cfg['scale']/self.aspect
                sy = cfg['scale']
                window = (x-.7*sx, y-1.2*sy, x+.7*sx, y+.9*sy)
                covered = any(rect_overlap(window, p.box) for p in people)
                if covered or candidates:
                    reason = 'OCCLUDED_OR_UNMATCHED'
                else:
                    present, hand, reason = False, None, 'ABSENT_EVIDENCE'
                # Old binding is never emitted as current presence.
                if seat in self.links and now-self.links[seat]['last'] > self.gap_s:
                    self.links.pop(seat)
            result.append({'seat': seat, 'present': present, 'hand_up': hand,
                           'visible': present is not None, 'track_id': tid,
                           'match_score': round(matching, 3), 'reason': reason})
        return result

    def unknown(self, reason):
        return [{'seat': seat, 'present': None, 'hand_up': None, 'visible': False,
                 'track_id': None, 'match_score': 0., 'reason': reason}
                for seat in sorted(self.seats)]


def rect_overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
