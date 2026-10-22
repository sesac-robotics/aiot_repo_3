"""CLI smoke test: temporary DB, simulated Uno, real loopback TCP/UDP. Stop real hub first."""
def main():
    import json, socket, subprocess, sys, tempfile, time, threading
    from pathlib import Path
    r=Path(__file__).resolve().parents[3]
    sys.path[:0]=[str(r),str(r/'service/jarvis_control_service')]
    from common.node_client import NodeClient
    from common.protocol import VIDEO_HEADER
    messages=[]
    with tempfile.TemporaryDirectory() as d:
     sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
     log=open(Path(d)/'hub.log','w+')
     proc=subprocess.Popen([sys.executable,str(r/'service/jarvis_control_service/run_hub.py'),'--host','127.0.0.1','--port',str(port),'--db',str(Path(d)/'hub.db'),'--log-file','','--pantilt-sim'],cwd=r,stdout=log,stderr=log)
     vision=NodeClient('vision_service','127.0.0.1',port,on_message=messages.append)
     udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',5021));udp.settimeout(3)
     sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
     try:
      vision.start()
      deadline=time.time()+8
      while not vision.connected and time.time()<deadline:
       if proc.poll() is not None: raise RuntimeError('hub exited')
       threading.Event().wait(.05)
      assert vision.connected, 'TCP handshake failed'
      packet=VIDEO_HEADER.pack(1,1,time.time())+b'\xff\xd8test'
      sender.sendto(packet,('127.0.0.1',6001))
      assert udp.recv(65535)==packet,'UDP packet mismatch'
      for i in range(27):
       now=time.time()
       vision.send('seat_obs',value=dict(session='socket-smoke',frame_id=i,captured_at=now,processed_at=now,seats=[dict(seat=1,present=True,hand_up=True)]))
       threading.Event().wait(.1)
      target=[m for m in messages if m['type']=='track_target' and m['value'].get('seat')==1]
      assert target,'No target from central hold decision'
      gen=target[-1]['value']['gen']
      vision.send('track_obs',seat=1,value=dict(gen=gen,frame_id=100,captured_at=time.time(),status='LOST'))
      threading.Event().wait(.2)
      print(json.dumps(dict(tcp_connected=True,udp_relay_without_gui=True,central_question_hold=True,track_target_gen=gen,lost_without_coordinates=True)))
     finally:
      vision.stop();udp.close();sender.close();proc.terminate()
      try: proc.wait(timeout=5)
      except subprocess.TimeoutExpired:proc.kill();proc.wait()
      log.seek(0);contents=log.read()
      print(contents[-3000:])
      assert 'Traceback' not in contents, contents

if __name__ == "__main__":
    main()
