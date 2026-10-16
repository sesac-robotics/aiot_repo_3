"""
hub 서버 실행용 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
터미널에서는 `python3 run_hub.py` 또는 `python3 -m hub` 와 같다.

이 파일이 project1 폴더 맨 위에 있어야 common/, hub/ 를 찾을 수 있다.
"""

from hub.hub import main

if __name__ == "__main__":
    main()
