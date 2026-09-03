# r2_storage.py
import boto3
import os
from botocore.config import Config

def get_r2_client():
    return boto3.client(
        's3',
        endpoint_url=f"https://{os.getenv('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
        config=Config(signature_version='s3v4')
    )

def upload_video(prod_id: str, file_path: str) -> str:
    bucket = os.getenv('R2_BUCKET_NAME')
    key = f"videos/{prod_id}.mp4"
    
    client = get_r2_client()
    client.upload_file(file_path, bucket, key, ExtraArgs={'ContentType': 'video/mp4'})
    
    public_url = f"https://pub-{os.getenv('R2_ACCOUNT_ID')}.r2.dev/{key}"
    return public_url
