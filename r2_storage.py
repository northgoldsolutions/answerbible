# r2_storage.py
import boto3
import os
from botocore.config import Config

def get_r2_client():
    account_id = os.getenv('R2_ACCOUNT_ID')
    access_key = os.getenv('R2_ACCESS_KEY_ID')
    secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
    
    if not all([account_id, access_key, secret_key]):
        raise ValueError("Missing R2 credentials")
    
    return boto3.client(
        's3',
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version='s3v4')
    )

def upload_video(prod_id: str, file_path: str) -> str:
    bucket = os.getenv('R2_BUCKET_NAME')
    if not bucket:
        raise ValueError("Missing R2_BUCKET_NAME")
    
    key = f"videos/{prod_id}.mp4"
    client = get_r2_client()
    client.upload_file(file_path, bucket, key, ExtraArgs={'ContentType': 'video/mp4'})
    
    public_url = f"https://pub-{os.getenv('R2_ACCOUNT_ID')}.r2.dev/{key}"
    return public_url
