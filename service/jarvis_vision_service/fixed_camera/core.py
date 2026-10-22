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


def hand_state(person, aspect, threshold=.5, rise=.20, wrist_threshold=.3):
    """At least one reliably UP arm is enough; DOWN needs both arms.

    If shoulders overlap in profile, use a visible same-side torso as scale.
    Missing/degenerate scale yields UNKNOWN rather than a guessed boolean.
    wrist_threshold: 손목만 따로 낮춘다. 작게 찍힌 뒷줄 학생이 든 손목은 모델이 0.3~0.5 로만 잡아서,
    어깨와 같은 0.5 를 쓰면 손을 높이 들어도 '모름'이었다 (10/22 노트북 4석 시험: 3·4번 손들기 실패).
    한두 프레임 잘못 잡혀도 Control 이 2초·3회 이상 확인해야 질문이 되므로 낮춰도 된다.
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
        if not (valid_point(k, s, threshold) and valid_point(k, w, wrist_threshold)):
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
    """좌석마다 등록한 어깨 위치(anchor)·어깨 폭(scale)과 가장 잘 맞는 사람 한 명을 고른다.

    4석(앞줄 2 · 뒷줄 2)에서 좌석이 섞이지 않게 하는 규칙:
    - max_ratio: 등록 때보다 이 배수 넘게 크게 찍힌 사람은 그 좌석 사람이 아니다.
      앞줄 학생은 뒷줄 좌석보다 약 1.5~2배 크게 찍히므로, 예전 2.2배 허용이면 앞줄 학생이
      일어나거나 몸을 뒤로 젖힐 때 뒷줄 좌석으로 잡혔다.
    - 좌석 반경은 가장 가까운 다른 좌석까지 거리의 절반을 넘지 않는다 (두 좌석 범위가 겹치지 않음).
    - min_radius: 뒷줄은 작게 찍혀 등록 반경이 하한 0.055(약 26px)가 된다. 앉은 채 몸을 조금만 옮겨도
      범위를 벗어나 '모름'이 됐다 (10/22 시험: 4번 학생 어깨가 등록 위치에서 0.05 이동 → 모름 38%).
      녹화 재생에서 0.08이면 모름 38→22% (3번 24→16%), 빈자리 판정 비율은 그대로였다.
    - min_ratio 는 낮게 둔다. 몸을 옆으로 돌리면(옆 사람과 대화·필기) 어깨가 겹쳐 폭이 등록 때의 30~40%로
      줄어든다 (10/22 노트북 4석 시험: 뒷줄 학생 중앙값 0.35배 → 0.45 하한에 걸려 절반이 '모름').
      다른 줄 학생은 반경 제한 때문에 이 좌석 범위에 들어오지 못하므로 하한이 섞임을 막는 역할은 거의 없다.
    gap_s: 이 간격 안에서 같은 추적 ID 가 다시 보이면 같은 사람으로 이어 본다.
      Control 의 판정 간격(OBS_GAP_S 0.8초)과 맞춘다. 0.5초면 2 fps 아래에서 좌석이 영영 '확인 중'이었다.
    """
    def __init__(self, config, kpt_conf=.5, confirm_s=.6, gap_s=.8, min_ratio=.25, max_ratio=1.5, cover_frac=.35,
                 cover_by='seated', min_radius=.08):
        self.config = config
        self.aspect = config['aspect']
        self.seats = {int(s): v for s, v in config['seats'].items()}
        self.kpt_conf, self.confirm_s, self.gap_s = kpt_conf, confirm_s, gap_s
        self.min_ratio, self.max_ratio = min_ratio, max_ratio
        self.cover_frac, self.cover_by = cover_frac, cover_by
        self.radius = {}
        for s, v in self.seats.items():
            others = [distance(v['anchor'], o['anchor'], self.aspect) for t, o in self.seats.items() if t != s]
            self.radius[s] = min([max(v['radius'], min_radius)] + [d/2 for d in others])
        self.links = {}

    def reset(self):
        self.links.clear()

    def occluder(self, p, seated=False):
        """좌석을 가릴 수 있는 영역. 'box' = 사람 상자.
        'keypoints'(시험용): 실제로 보인 머리·어깨 점의 범위(+가슴 조금).
        'seated'(기본): 이번 프레임에 자기 좌석에 맞춰진 사람(seated=True)만 머리·어깨 범위, 나머지는 상자.
        앞줄 학생 상자는 머리부터 책상까지라 뒤에 앉은 뒷줄 좌석을 늘 덮었다 (10/22 시험: 2번 상자가 비어 있는
        4번 범위의 34~68%, 1번 상자가 3번 범위의 51~88% → 뒷줄 자리비움이 '모름'). 자기 좌석에 앉은 것이 확인된
        사람만 머리·어깨로 줄이고, 좌석에 안 맞는 사람(서 있음·지나감·화면 끝으로 밀려남)은 상자 그대로 쓴다.
        'keypoints' 의 잘못된 자리비움은 2번 학생이 화면 끝으로 밀려 자기 좌석에서 벗어났을 때 생겼는데,
        그 사람은 좌석에 안 맞으므로 'seated' 에서는 상자로 남는다.
        10/22 2m 기록 재생: keypoints 는 앞줄 바로 뒤 3번 자리비움을 잡았지만, 앉아 있는데 잠깐 놓친
        프레임 때문에 잘못된 자리비움이 7건 더 생겨 기본으로 쓰지 않는다.
        사람 상자는 머리·어깨 주변의 빈 공간까지 덮어서, 2m 카메라에서 앞줄 1번 상자가 비어 있는 뒷줄 3번
        범위의 50~53% 를 늘 덮어 3번 자리비움이 영영 '모름'이었다 (10/22). 머리·어깨 점이 2개 미만이면
        어디를 가리는지 모르므로 상자 전체를 쓴다 (보수적)."""
        if not (self.cover_by == 'keypoints' or (self.cover_by == 'seated' and seated)):
            return p.box
        pts = [p.keypoints[i] for i in range(7) if len(p.keypoints) > i and p.keypoints[i][2] >= .3]
        if len(pts) < 2:
            return p.box
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        return (min(xs), min(ys), max(xs), max(ys) + .25*(max(xs)-min(xs)))

    def dedupe(self, people):
        """같은 사람이 두 번 잡힌 것을 하나로 합친다.

        작게 찍힌 뒷줄 학생은 YOLO 가 67~68% 겹친 상자 두 개를 내고(기본 NMS 는 70% 이상만 지움),
        추적기가 둘 다에 ID 를 준다. 그러면 한 좌석에 후보가 둘이라 AMBIGUOUS 가 되거나, 둘이 번갈아 뽑혀
        ID 가 바뀐 것으로 보고 MATCH_PENDING 으로 돌아간다 (10/22 노트북 4석 시험: 3·4번 손들기 실패의 원인).
        서로 다른 두 사람의 어깨 가운데가 어깨 폭의 1/4 보다 가까울 수는 없으므로, 그만큼 가깝고 크기가
        비슷하면 같은 사람으로 본다. 남길 쪽: 이미 좌석에 이어진 ID → 신뢰도가 높은 쪽.
        """
        linked = {l['id'] for l in self.links.values()}
        order = sorted(people, key=lambda p: (p.track_id in linked, p.confidence), reverse=True)
        kept = []
        for p in order:
            a, s = p.anchor(self.kpt_conf), p.scale(self.aspect, self.kpt_conf)
            dup = False
            if a is not None and s:
                for q in kept:
                    b, t = q.anchor(self.kpt_conf), q.scale(self.aspect, self.kpt_conf)
                    if b is not None and t and distance(a, b, self.aspect) < .25*max(s, t) and .7 <= s/t <= 1.4:
                        dup = True
                        break
            if not dup:
                kept.append(p)
        return kept

    def observe(self, people, now):
        # Prevent uncontrolled combinatorial work on false detections.
        if len(people) > 12:
            return self.unknown('TOO_MANY_PEOPLE')
        people = self.dedupe(people)
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
                d = distance(a, cfg['anchor'], self.aspect)/self.radius[seat]
                ratio = scale/cfg['scale']
                if d > 1 or not self.min_ratio <= ratio <= self.max_ratio:
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
        seated = {i for i in choices if i is not None}       # 이번 프레임에 어느 좌석엔가 맞춰진 사람
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
                        if old is None or now-old['last'] > self.gap_s:
                            old = {'id': tid, 'since': now, 'last': now}
                        elif old['id'] != tid:
                            # 같은 좌석에서 끊김 없이(gap_s 안) 이어지는데 추적 ID 만 바뀐 경우(가림·자세 변화로
                            # 추적기가 새 ID 를 줌)는 같은 사람으로 이어 본다. 예전에는 처음부터 다시 confirm_s 를 기다려
                            # 손을 드는 동안 '확인 중'이 되었다 (10/22 실물 시험). 좌석 위치·크기 조건은 그대로 적용된다.
                            old = {'id': tid, 'since': old['since'], 'last': now}
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
                # A person box covering enough of it can conceal the seat, regardless of ID.
                # 모서리만 걸친 상자는 가린 것이 아니다: 카메라를 높이 두면 뒷줄 학생 상자가 앞줄 좌석 범위에
                # 6~31% 걸쳐 앞줄 자리비움이 영영 '모름'이었다 (10/22 2m 시험, 2번). 앞줄에 실제로 가려진 뒷줄은
                # 46~59% 덮이므로 cover_frac(35%) 이상일 때만 가림으로 본다.
                x, y = cfg['anchor']
                sx = cfg['scale']/self.aspect
                sy = cfg['scale']
                window = (x-.7*sx, y-1.2*sy, x+.7*sx, y+.9*sy)
                covered = any(overlap_fraction(window, self.occluder(p, j in seated)) >= self.cover_frac
                              for j, p in enumerate(people))
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


def overlap_fraction(window, box):
    """box 가 window 넓이의 몇 % 를 덮는지 (0~1)"""
    ix = max(0., min(window[2], box[2]) - max(window[0], box[0]))
    iy = max(0., min(window[3], box[3]) - max(window[1], box[1]))
    area = (window[2]-window[0]) * (window[3]-window[1])
    return ix*iy/area if area > 0 else 0.
