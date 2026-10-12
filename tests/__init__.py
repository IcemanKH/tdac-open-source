import os
import tempfile

# 测试期间把用户数据目录重定向到临时目录，避免触碰真实 %LOCALAPPDATA%。
os.environ.setdefault("TDAC_DATA_DIR", os.path.join(tempfile.gettempdir(), "tdac-tests-data"))
