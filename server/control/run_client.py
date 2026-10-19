"""
노드 템플릿 실행용 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(control 서버는 먼저 run_hub.py 로 켜 둔다)

노드 이름은 tools/node_template.py 위쪽의 NODE 를,
control IP 는 config/common.yaml 의 control_host 를 고친다.
전체 흐름 시험은 tools/fake_node.py (scenario) 를 쓴다.

터미널에서는:  python3 run_client.py --server 127.0.0.1
"""

from tools.node_template import main

if __name__ == "__main__":
    main()
