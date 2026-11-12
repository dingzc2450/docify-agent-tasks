import os
import sys

# 让测试能直接 import feishu_bot 目录下的模块（与 mock_event.py 同一约定）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
