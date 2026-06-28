"""GitHub integration via PyGithub.

Wraps the GitHub API for the operations the agent needs: fetching the raw logs
of a failed workflow job. Uses the modern ``Auth.Token`` API and a lazily
created client so importing this module never requires a token.
"""

from __future__ import annotations

import requests
from github import Auth, Github

from .config import get_settings

# GitHub REST endpoint for a single job's logs (302-redirects to a signed URL).
_JOB_LOGS_URL = "https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"

_client: Github | None = None


def _require_token() -> str:
    token = get_settings().github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set; cannot call the GitHub API")
    return token


def get_client() -> Github:
    """Return a cached PyGithub client, creating it on first use."""
    global _client
    if _client is None:
        _client = Github(auth=Auth.Token(_require_token()))
    return _client


def fetch_failed_job_logs(repo_name: str, run_id: int) -> str:
    """Fetch the raw logs for the first failed job in a workflow run.

    Returns the log text, or an empty string if no failed job/logs are found.
    """
    token = _require_token()
    repo = get_client().get_repo(repo_name)
    run = repo.get_workflow_run(run_id)

    for job in run.jobs():
        if job.conclusion == "failure":
            url = _JOB_LOGS_URL.format(repo=repo_name, job_id=job.id)
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.text
            return ""
    return ""


class GitHubClient:
    """Backward-compatible wrapper retained for existing imports/tests."""

    def __init__(self, token: str | None = None) -> None:
        self._gh = Github(auth=Auth.Token(token or _require_token()))

    def get_repo(self, full_name: str):
        return self._gh.get_repo(full_name)

    def create_issue(self, full_name: str, title: str, body: str = ""):
        return self.get_repo(full_name).create_issue(title=title, body=body)
