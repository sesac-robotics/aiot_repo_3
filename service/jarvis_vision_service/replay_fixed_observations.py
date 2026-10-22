"""Replay JSONL observations through the LOCAL decision simulator (no YOLO/network)."""
import argparse
import json
from fixed_camera.decision_demo import DecisionDemo


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('observations')
    p.add_argument('--question-hold',type=float,default=2.)
    p.add_argument('--away-hold',type=float,default=10.)
    p.add_argument('--return-hold',type=float,default=2.)
    a=p.parse_args()
    demo=None
    session=None
    with open(a.observations,encoding='utf-8') as f:
        for line in f:
            obs=json.loads(line)
            if obs['session'] != session:
                session=obs['session']
                demo=DecisionDemo(a.question_hold,a.away_hold,a.return_hold)
            for ev in demo.process(obs,obs['captured_at']):
                print(json.dumps(ev,ensure_ascii=False))
    print(json.dumps({'remaining_queue':demo.queue() if demo else []},ensure_ascii=False))

if __name__=='__main__':main()
