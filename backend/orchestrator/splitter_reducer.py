"""
R.I.F.T. Orchestrator Backend (Job Splitter & Reducer)
Handles:
1. Job Submission & Splitting into SQS chunks & DynamoDB tracking
2. Reducer consolidation of completed chunks into single output
"""

import json
import uuid
import time
import os
import boto3

REGION = os.getenv("AWS_REGION", "ap-southeast-2")
dynamodb = boto3.resource("dynamodb", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

JOBS_TABLE = os.getenv("JOBS_TABLE", "rift-jobs")
CHUNKS_TABLE = os.getenv("CHUNKS_TABLE", "rift-chunks")
QUEUE_URL = os.getenv("QUEUE_URL", "https://sqs.ap-southeast-2.amazonaws.com/903859732456/rift-chunks-queue")
BUCKET_NAME = os.getenv("BUCKET_NAME", "rift-storage-903859732456-ap-southeast-2")

def submit_and_split_job(job_type: str, total_chunks: int = 5, params: dict = None) -> dict:
    """
    Splits job into chunks, registers in DynamoDB, and enqueues chunks in SQS.
    """
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    params = params or {}
    
    jobs_tbl = dynamodb.Table(JOBS_TABLE)
    chunks_tbl = dynamodb.Table(CHUNKS_TABLE)

    # 1. Register Job in DynamoDB
    jobs_tbl.put_item(Item={
        "jobId": job_id,
        "type": job_type,
        "status": "PROCESSING",
        "chunkCount": total_chunks,
        "chunksDone": 0,
        "createdAt": int(time.time()),
        "params": params
    })

    # 2. Split into chunks & enqueue into SQS
    chunk_ids = []
    for i in range(total_chunks):
        chunk_id = f"chunk-{i:03d}"
        chunk_params = dict(params)
        
        if job_type == "monte_carlo":
            chunk_params["seed"] = 1000 + i * 17
            chunk_params["samples"] = int(params.get("samples_per_chunk", 200_000))
        elif job_type == "matrix_multiply":
            chunk_params["row_start"] = i * 20
            chunk_params["row_end"] = (i + 1) * 20
            chunk_params["dim"] = int(params.get("dim", 200))
        elif job_type == "image_tile":
            chunk_params["tile_id"] = chunk_id
            chunk_params["filter_type"] = params.get("filter", "edge_enhance")

        # Save chunk state
        chunks_tbl.put_item(Item={
            "jobId": job_id,
            "chunkId": chunk_id,
            "status": "QUEUED",
            "attempts": 0,
            "params": chunk_params
        })

        # Send SQS message for available PCs to pull
        msg_body = json.dumps({
            "jobId": job_id,
            "chunkId": chunk_id,
            "type": job_type,
            "params": chunk_params
        })
        sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=msg_body)
        chunk_ids.append(chunk_id)

    return {
        "jobId": job_id,
        "type": job_type,
        "chunkCount": total_chunks,
        "status": "QUEUED",
        "chunks": chunk_ids
    }

def reduce_job_results(job_id: str, chunk_results: list) -> dict:
    """
    Merges partial chunk outputs into the final consolidated result.
    """
    jobs_tbl = dynamodb.Table(JOBS_TABLE)
    
    job_item = jobs_tbl.get_item(Key={"jobId": job_id}).get("Item", {})
    job_type = job_item.get("type", "unknown")

    final_output = {}
    if job_type == "monte_carlo":
        total_samples = sum(c.get("samples", 0) for c in chunk_results)
        total_inside = sum(c.get("inside_circle", 0) for c in chunk_results)
        final_pi = float(4.0 * total_inside / max(total_samples, 1))
        final_output = {
            "type": "monte_carlo",
            "total_samples": total_samples,
            "total_inside_circle": total_inside,
            "final_estimated_pi": final_pi,
            "error_percentage": round(abs(final_pi - 3.14159265) / 3.14159265 * 100, 4)
        }
    elif job_type == "matrix_multiply":
        total_checksum = sum(c.get("checksum", 0.0) for c in chunk_results)
        final_output = {
            "type": "matrix_multiply",
            "chunks_merged": len(chunk_results),
            "consolidated_checksum": round(total_checksum, 4)
        }
    elif job_type == "image_tile":
        final_output = {
            "type": "image_tile",
            "tiles_processed": len(chunk_results),
            "status": "READY_FOR_PLAYBACK"
        }

    # Convert any floats to Decimal for DynamoDB compatibility
    def _to_dynamo_safe(val):
        from decimal import Decimal
        if isinstance(val, float):
            return Decimal(str(val))
        if isinstance(val, dict):
            return {k: _to_dynamo_safe(v) for k, v in val.items()}
        if isinstance(val, list):
            return [_to_dynamo_safe(v) for v in val]
        return val

    dynamo_safe_output = _to_dynamo_safe(final_output)

    # Update job state in DynamoDB
    jobs_tbl.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET #s = :s, chunksDone = :d, finalResult = :r, completedAt = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "COMPLETED",
            ":d": len(chunk_results),
            ":r": dynamo_safe_output,
            ":t": int(time.time())
        }
    )

    return {
        "jobId": job_id,
        "status": "COMPLETED",
        "result": final_output
    }
