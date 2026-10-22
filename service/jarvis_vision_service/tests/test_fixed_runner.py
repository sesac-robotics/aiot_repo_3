"""Exercise the runner with synthetic inference, not a claim of YOLO accuracy."""
import json
from pathlib import Path
import queue
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fixed_camera import app


class RunnerSmoke(unittest.TestCase):
    def test_frame_inference_observation_logging_and_cleanup(self):
        class Frame:
            shape=(480,640,3)
        class Tensor:
            def __init__(self,data):self.data=data
            def cpu(self):return self
            def tolist(self):return self.data
            def int(self):return self
        class Result:
            boxes=None
            keypoints=None
            def plot(self):return Frame()
        class Model:
            task='pose'
            def __init__(self,path):pass
            def track(self,frame,**kwargs):return [Result()]
        class Source:
            def __init__(self,*args):
                self.latest=self;self.eof=False;self.error=None;self.i=0;self.closed=False
            def get(self,timeout):
                self.i+=1
                if self.i>2:
                    self.eof=True
                    raise queue.Empty
                return self.i,time.time(),time.monotonic(),Frame()
            def close(self):self.closed=True
        cv=types.SimpleNamespace(WINDOW_AUTOSIZE=1,EVENT_LBUTTONDOWN=1,WND_PROP_VISIBLE=1,
            namedWindow=lambda *a:None,setMouseCallback=lambda *a:None,
            waitKey=lambda *a:-1,getWindowProperty=lambda *a:1,imshow=lambda *a:None,
            destroyAllWindows=lambda:None,putText=lambda *a:None,circle=lambda *a:None)
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            model=d/'pose.pt';model.touch()
            cfg=d/'seats.json'
            cfg.write_text(json.dumps({'version':1,'camera_id':1,'aspect':4/3,
                'seats':{'1':{'anchor':[.3,.5],'scale':.1,'radius':.1}}}))
            argv=['runner','--model',str(model),'--seats',str(cfg),'--log-dir',str(d/'logs')]
            with patch.object(sys,'argv',argv), patch.dict(sys.modules,{
                'cv2':cv,'ultralytics':types.SimpleNamespace(YOLO=Model)}),patch.object(app,'FrameSource',Source):
                app.main()
            files=list((d/'logs').glob('*.observations.jsonl'))
            self.assertEqual(len(files),1)
            records=[json.loads(line) for line in files[0].read_text().splitlines()]
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]['schema'],'fixed_seat_obs_v1')
            self.assertFalse(records[0]['seats'][0]['present'])
            self.assertEqual(list((d/'logs').glob('*.events.jsonl'))[0].read_text(),'')

if __name__=='__main__':unittest.main()
