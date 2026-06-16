import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

    if not DISCORD_TOKEN:
        raise ValueError("Thiếu DISCORD_TOKEN trong file .env")

#doc cau hinh 