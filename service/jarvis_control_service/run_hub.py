"""
control(hub) 서버 실행용 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
터미널에서는 `python3 run_hub.py` 또는 `python3 -m hub` 와 같다.

hub/ 는 이 폴더에서, common/ 은 저장소 최상위(aiot_repo_3/common)에서 찾는다 (hub/__init__.py 가 경로를 넣음).
"""

from hub.hub import main

if __name__ == "__main__":
    main()
