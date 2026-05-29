import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
import sys

# Api Settings
class Settings(BaseSettings):
    app_name: str
    groq_api_key:str
    MAX_RETRIES: int = 3
    debug_mode: bool = False

    model_config = SettingsConfigDict(env_file="Circuit_breaker/.env", extra="ignore")

settings = Settings()

# Logger Settings

logger = logging.getLogger("Circuit_Breaker")
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

file_handler = logging.FileHandler("Circuit_breaker/app/Circuit_breaker.log")
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)
