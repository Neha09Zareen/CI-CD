"""FastAPI application entrypoint for the AI CI/CD Agent.

Receives GitHub Actions workflow webhooks, detects pipeline failures,
and fetches the raw logs for the failed job. Run locally with:
    uvicorn src.main:app --reload
"""

from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from github import Github

from .cascade_parser import cascade_chunk_logs
from .cascade_flow import analyze_logs_with_cascadeflow

load_dotenv()

app = FastAPI(title="AI CI/CD Agent")

# Initialize PyGithub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN is missing from .env")

gh = Github(GITHUB_TOKEN)


def fetch_failed_job_logs(repo_name: str, run_id: int) -> str:
    """Fetches the raw logs for the failed job in a workflow run."""
    repo = gh.get_repo(repo_name)
    run = repo.get_workflow_run(run_id)

    # Iterate through jobs to find the specific one that failed
    for job in run.jobs():
        if job.conclusion == "failure":
            # PyGithub doesn't expose a direct method that returns the log
            # text, so we hit the GitHub REST API directly for the raw log
            # using our token. GitHub responds with a 302 redirect to a
            # short-lived signed URL; requests follows it automatically.
            log_url = (
                f"https://api.github.com/repos/{repo_name}"
                f"/actions/jobs/{job.id}/logs"
            )
            headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
            response = requests.get(log_url, headers=headers)
            if response.status_code == 200:
                return response.text
            print(f"Failed to fetch logs: {response.status_code}")
            return ""
    return ""


@app.post("/webhook")
async def github_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Extract required fields. Non-workflow_run events (push, ping, etc.)
    # are valid deliveries we simply don't act on, so acknowledge with 200.
    workflow_run = payload.get("workflow_run")
    if not workflow_run:
        return {"status": "ignored", "reason": "No workflow_run in payload"}

    run_id = workflow_run.get("id")
    conclusion = workflow_run.get("conclusion")
    repo_name = payload.get("repository", {}).get("full_name")

    if conclusion == "failure":
        print(f"🚨 Pipeline failure detected! Run ID: {run_id} in {repo_name}")

        # 1. Fetch the raw logs
        raw_logs = fetch_failed_job_logs(repo_name, run_id)
        if not raw_logs:
            return {"status": "error", "message": "Could not retrieve logs."}

        print(f"✅ Successfully fetched logs. Length: {len(raw_logs)} characters.")

        # 3. Pre-parse logs into overlapping chunks via Cascade
        chunks = cascade_chunk_logs(raw_logs)
        print(f"📦 Parsed into {len(chunks)} chunks via Cascade.")

        # 4. Route chunks via cascadeflow and generate a fix
        fix = analyze_logs_with_cascadeflow(chunks)
        print("🛠️ cascadeflow analysis complete.")

        # TODO: Step 5 - Store/Lookup in Hindsight

        return {"status": "processing", "run_id": run_id, "fix": fix}

    return {"status": "ignored", "reason": f"Conclusion was {conclusion}"}
