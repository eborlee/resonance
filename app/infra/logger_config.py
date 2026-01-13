# logger_config.py
import logging
import os
from logging.handlers import RotatingFileHandler

class LevelColorFormatter(logging.Formatter):
    COLOR_MAP = {
        logging.DEBUG: "\033[33m",    # 橙
        logging.INFO: "\033[32m",     # 绿
        logging.WARNING: "\033[35m",  # 紫
        logging.ERROR: "\033[31m",    # 红
        logging.CRITICAL: "\033[41m", # 红底
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        level_color = self.COLOR_MAP.get(record.levelno, "")
        record.levelname = f"{level_color}{record.levelname}{self.RESET}"
        record.name = f"{level_color}{record.name}{self.RESET}"
        return super().format(record)

def setup_logging(log_path: str, max_bytes: int = 10*1024*1024, backup_count: int = 5):
    # 确保目录存在
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)  # ✅ 修改这里控制 root 默认日志级别

    # 清除旧 handler（避免重复）
    root_logger.handlers.clear()

    # Console handler（带颜色）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = LevelColorFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler（不带颜色，避免乱码）
    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # ✅ 设置各个模块的日志级别（你可以写在 settings.py 或 logging.yaml 中）
    module_log_levels = {
        "app.domain": logging.DEBUG,
        "app.adpaters": logging.DEBUG,
        "app.infra": logging.DEBUG,
        "app.services": logging.DEBUG,
        "app.adpaters": logging.DEBUG,
    }

    for module_name, level in module_log_levels.items():
        logging.getLogger(module_name).setLevel(level)