# config.py
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "sqlite:///./answers_in_faith.db"
    openai_api_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "josh"
    youtube_client_secrets_path: str = "config/client_secret.json"
    youtube_credentials_path: str = "config/youtube_credentials.json"
    ffmpeg_path: str = "ffmpeg"
    output_dir: str = "./output"
    max_scene_duration: int = 15

    class Config:
        env_file = ".env"

settings = Settings()

os.makedirs(settings.output_dir, exist_ok=True)
os.makedirs(f"{settings.output_dir}/audio", exist_ok=True)
os.makedirs(f"{settings.output_dir}/visuals", exist_ok=True)
os.makedirs(f"{settings.output_dir}/final", exist_ok=True)
