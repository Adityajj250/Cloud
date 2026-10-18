"""
Test Distributed Splitter & Reducer Pipeline with SQS and DynamoDB
"""

import sys
import os
import boto3

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from splitter_reducer import submit_and_split_job, reduce_job_results

# Explicitly use rift-admin profile session
session = boto3.Session(profile_name="rift-admin", region_name="ap-southeast-2")
import splitter_reducer
splitter_reducer.dynamodb = session.resource("dynamodb", region_name="ap-southeast-2")
splitter_reducer.sqs = session.client("sqs", region_name="ap-southeast-2")

print(">>> Testing Job Splitter (Monte Carlo)...")
split_res = splitter_reducer.submit_and_split_job("monte_carlo", total_chunks=4, params={"samples_per_chunk": 100_000})
job_id = split_res["jobId"]
print(f"  * Created Job ID: {job_id} with {split_res['chunkCount']} chunks in SQS")

# Simulate 4 worker results from distributed nodes
mock_chunk_results = [
    {"samples": 100_000, "inside_circle": 78540},
    {"samples": 100_000, "inside_circle": 78512},
    {"samples": 100_000, "inside_circle": 78590},
    {"samples": 100_000, "inside_circle": 78550},
]

print(">>> Testing Reducer...")
reduced = splitter_reducer.reduce_job_results(job_id, mock_chunk_results)
print(f"  * Consolidated Result: Estimated Pi = {reduced['result']['final_estimated_pi']} (Error: {reduced['result']['error_percentage']}%)")
assert reduced["status"] == "COMPLETED"
print(">>> SPLITTER & REDUCER TEST PASSED! <<<")
