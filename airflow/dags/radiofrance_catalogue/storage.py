"""Shared S3 locations and CLI access for the Radio France DAGs."""
import json
import os
import subprocess
from pathlib import Path
from typing import TypedDict, cast

PROJECT = Path(__file__).resolve().parents[3]


class CacheConfig(TypedDict, total=False):
    bucket: str
    region: str


def cache_config() -> CacheConfig:
    """Read optional local configuration; CI supplies environment values."""
    path = PROJECT / 'aws/cache-config.json'
    return cast(CacheConfig, json.loads(path.read_text())) if path.exists() else {}


def region() -> str:
    return os.environ.get('RF_S3_REGION') or cache_config().get('region', 'eu-west-2')


def aws(*arguments: str) -> None:
    subprocess.run(['aws', *arguments, '--region', region()], check=True)


def uri(show: str) -> str:
    return s3_uri(f'data/episodes/{show}.csv')


def s3_uri(key: str) -> str:
    bucket = os.environ.get('RF_S3_BUCKET') or cache_config().get('bucket')
    if not bucket:
        raise ValueError('Set RF_S3_BUCKET or supply local aws/cache-config.json')
    return f's3://{bucket}/{key}'


class StoredObject(TypedDict):
    Key: str
    Size: int


def list_objects(prefix: str) -> list[StoredObject]:
    """List every object under a prefix using AWS CLI automatic pagination."""
    bucket = s3_uri('').removeprefix('s3://').rstrip('/')
    result = subprocess.run([
        'aws', 's3api', 'list-objects-v2', '--bucket', bucket, '--prefix', prefix,
        '--query', 'Contents[].{Key:Key,Size:Size}', '--output', 'json', '--region', region(),
    ], check=True, capture_output=True, text=True)
    objects = json.loads(result.stdout) or []
    if not isinstance(objects, list):
        raise ValueError('Invalid S3 object listing')
    return cast(list[StoredObject], objects)
