"""The network volume over RunPod's S3-compatible API, so the app (on a laptop or anywhere) can put inputs on the
volume and fetch outputs from it while no pod is running.

Environment:
  RUNPOD_VOLUME_ID       the network volume id (the bucket name)
  RUNPOD_S3_ENDPOINT     https://s3api-<datacenter>.runpod.io  (EU-RO-1 -> https://s3api-eu-ro-1.runpod.io)
  RUNPOD_S3_KEY / RUNPOD_S3_SECRET   an S3 API key pair from RunPod Settings (not the ordinary API key)
  RUNPOD_S3_REGION       the datacenter id, e.g. eu-ro-1 (defaults from the endpoint host)
Object keys are the volume's own paths: creator-data/input/<file>, creator-data/output/motion_swap/<job>/<file>.
"""
import os
from pathlib import Path

PREFIX = os.environ.get("RUNPOD_VOLUME_PREFIX", "creator-data")


def configured():
    return all(os.environ.get(k) for k in ("RUNPOD_VOLUME_ID", "RUNPOD_S3_ENDPOINT", "RUNPOD_S3_KEY", "RUNPOD_S3_SECRET"))


def _client():
    import boto3
    from botocore.config import Config

    endpoint = os.environ["RUNPOD_S3_ENDPOINT"].rstrip("/")
    region = os.environ.get("RUNPOD_S3_REGION") or endpoint.split("s3api-", 1)[-1].split(".", 1)[0]
    return boto3.client("s3", endpoint_url=endpoint, region_name=region,
                        aws_access_key_id=os.environ["RUNPOD_S3_KEY"], aws_secret_access_key=os.environ["RUNPOD_S3_SECRET"],
                        config=Config(signature_version="s3v4", retries={"max_attempts": 4}, read_timeout=600))


def bucket():
    return os.environ["RUNPOD_VOLUME_ID"]


def upload(local_path, key):
    """Put one file on the volume (multipart automatically above 8 MB; RunPod caps a single PUT at 500 MB)."""
    from boto3.s3.transfer import TransferConfig

    _client().upload_file(str(local_path), bucket(), key, Config=TransferConfig(multipart_threshold=8 * 2**20, multipart_chunksize=64 * 2**20))
    return key


def download(key, local_path):
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    _client().download_file(bucket(), key, str(local_path))
    return local_path


def listing(prefix):
    """[(key, size, last_modified), ...] under a prefix."""
    out, token = [], None
    c = _client()
    while True:
        kw = {"Bucket": bucket(), "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        r = c.list_objects_v2(**kw)
        out += [(o["Key"], o["Size"], o["LastModified"]) for o in r.get("Contents", [])]
        token = r.get("NextContinuationToken")
        if not token:
            return out


def exists(key):
    try:
        _client().head_object(Bucket=bucket(), Key=key)
        return True
    except Exception:
        return False


def sync_jobs(jobs_dir):
    """Copy every job.json on the volume into the local job store (new or changed ones only). Returns the count."""
    jobs_dir = Path(jobs_dir)
    n = 0
    for key, size, _ in listing(f"{PREFIX}/output/_jobs/"):
        if not key.endswith("/job.json"):
            continue
        job_id = key.rsplit("/", 2)[-2]
        local = jobs_dir / job_id / "job.json"
        if local.exists() and local.stat().st_size == size:
            continue
        download(key, local)
        n += 1
    return n


def job_file_key(job_id, name):
    return f"{PREFIX}/output/_jobs/{job_id}/{name}"


def input_key(name):
    return f"{PREFIX}/input/{name}"


def output_key(rel):
    return f"{PREFIX}/output/{str(rel).replace(chr(92), '/')}"
