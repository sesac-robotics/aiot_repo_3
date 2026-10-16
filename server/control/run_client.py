"""
가짜 노드 실행용 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(hub 서버는 먼저 run_hub.py 로 켜 둔다)

모드(all / fixedcam / student3 ...)는 tools/fake_node.py 위쪽의 NODE 를,
hub IP 는 config/common.yaml 의 hub_host 를 고친다.

터미널에서는:  python3 run_client.py --node student3 --server 127.0.0.1
"""

from tools.node_template import main

if __name__ == "__main__":
    main()
