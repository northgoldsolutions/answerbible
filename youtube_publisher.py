# youtube_publisher.py
import os
import pickle
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from config import settings

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def get_youtube_service():
    creds = None
    if os.path.exists(settings.youtube_credentials_path):
        with open(settings.youtube_credentials_path, "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                settings.youtube_client_secrets_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(settings.youtube_credentials_path, "wb") as token:
            pickle.dump(creds, token)
    return build("youtube", "v3", credentials=creds)

def upload_video(production, video_path: str, privacy_status: str = "private"):
    youtube = get_youtube_service()
    body = {
        "snippet": {
            "title": production.title,
            "description": production.description,
            "tags": production.keywords.split(",") if production.keywords else [],
            "categoryId": "27"
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    return response["id"]
