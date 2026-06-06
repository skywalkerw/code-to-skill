# code-to-skill: 从知识库和代码提取并优化 Agent Skill

import logging

from .time_utils import LocalTimeFormatter

_handler = logging.StreamHandler()
_handler.setFormatter(LocalTimeFormatter(
    fmt="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
