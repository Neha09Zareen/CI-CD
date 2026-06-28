"""GitHub integration via PyGithub.

Thin wrapper around the GitHub API for the operations the agent needs
(reading repos, opening issues/PRs, etc.). Authentication uses the
GITHUB_TOKEN from the environment.
"""

from __future__ import annotations

import os

from github import Github


class GitHubClient:
    """Wrapper around PyGithub for common repo operations."""

    def __init__(self, token: str | None = None) -> None:
        token = token or os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN is not set")
        self._gh = Github(token)

    def get_repo(self, full_name: str):
        """Return a repository handle, e.g. 'owner/name'."""
        return self._gh.get_repo(full_name)

    def create_issue(self, full_name: str, title: str, body: str = ""):
        """Open a new issue on the given repository."""
        return self.get_repo(full_name).create_issue(title=title, body=body)
