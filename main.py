from bot.client import DiscordBot
from bot.config import Config
from bot.services.logger import setup_logger


def main():
    logger = setup_logger()
    bot = DiscordBot(command_prefix=Config.COMMAND_PREFIX)
    bot.run(Config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()