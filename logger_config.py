import logging
import datetime

class EmojiFormatter(logging.Formatter):
    LEVEL_EMOJIS = {
        logging.DEBUG: "🐞",
        logging.INFO: "ℹ️",
        logging.WARNING: "⚠️",
        logging.ERROR: "❌",
        logging.CRITICAL: "🔥"
    }

    LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",    # cyan
        logging.INFO: "\033[32m",     # green
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",    # red
        logging.CRITICAL: "\033[41m", # red background
    }

    RESET = "\033[0m"

    def format(self, record):
        time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        emoji = self.LEVEL_EMOJIS.get(record.levelno, "ℹ️")
        color = self.LEVEL_COLORS.get(record.levelno, "")
        message = record.getMessage()
        return f"{color}[{time}] {emoji} {record.levelname}: {message}{self.RESET}"


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(EmojiFormatter())
    logger.addHandler(ch)
    return logger
