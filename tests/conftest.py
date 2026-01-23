"""Pytest fixtures for Giji tests."""

import pytest


@pytest.fixture
def sample_bug_issue():
    """Sample GitHub bug issue."""
    return {
        "number": 42,
        "title": "[BUG]: Incorrect API response format in ECS documentation",
        "body": """### User's Impact

Users cannot understand the correct response format when calling the ECS API, leading to integration failures.

### Document URL

https://docs.otc.t-systems.com/api-ref/ecs/ecs_api_0001.html

### Description

Bug detail description:
The API response shows wrong format for the `status` field. Documentation says it returns a string, but actual API returns an integer.

Preconditions:
User has valid credentials and ECS instance running.

How to reproduce:
1. Call GET /v2/servers/{server_id}
2. Check the `status` field in response

Result of the issue:
Documentation shows `"status": "ACTIVE"` but API returns `"status": 1`

Expected result:
Documentation should match actual API response format.

### Additional Context

Screenshot attached:
![error](https://github.com/user-attachments/assets/abc123.png)
""",
        "html_url": "https://github.com/opentelekomcloud-docs/elastic-cloud-server/issues/42",
        "labels": [{"name": "bug"}],
        "state": "open"
    }


@pytest.fixture
def sample_bug_issue_no_template():
    """Bug issue without template format."""
    return {
        "number": 43,
        "title": "[BUG]: Something is broken",
        "body": "This is just a plain text description without template.",
        "html_url": "https://github.com/opentelekomcloud-docs/elastic-cloud-server/issues/43",
        "labels": [{"name": "bug"}],
        "state": "open"
    }


@pytest.fixture
def sample_demand_issue():
    """Sample GitHub demand issue."""
    return {
        "number": 100,
        "title": "[DEMAND]: Add documentation for new Resize API",
        "body": """### Summary

Need documentation for the new Resize API endpoint that allows changing instance size.

### Feature Description

Feature overview:
The Resize API allows users to change instance size without downtime.

Business context:
Customers need to scale their instances dynamically based on load.

Target audience:
Cloud administrators and DevOps engineers.

Success criteria:
Complete API reference with request/response examples and UMN guide with step-by-step instructions.

Additional requirements:
Must include information about supported instance types and limitations.

### Documents Requested

- [x] API
- [x] UMN

### Additional Context

Priority for Q1 release. Related to feature ticket FEAT-1234.
""",
        "html_url": "https://github.com/opentelekomcloud-docs/elastic-cloud-server/issues/100",
        "labels": [{"name": "demand"}],
        "state": "open"
    }


@pytest.fixture
def sample_unlabeled_issue():
    """Issue without any labels (for bulk import)."""
    return {
        "number": 200,
        "title": "Fix typo in documentation",
        "body": "There's a typo on page 5.",
        "html_url": "https://github.com/opentelekomcloud-docs/elastic-cloud-server/issues/200",
        "labels": [],
        "state": "open"
    }


@pytest.fixture
def sample_imported_issue():
    """Issue already imported to Jira."""
    return {
        "number": 300,
        "title": "[BUG] Already imported",
        "body": "This was already imported.",
        "html_url": "https://github.com/opentelekomcloud-docs/elastic-cloud-server/issues/300",
        "labels": [{"name": "bug"}, {"name": "imported-to-jira"}],
        "state": "open"
    }


@pytest.fixture
def sample_pull_request():
    """Pull request (should be skipped)."""
    return {
        "number": 400,
        "title": "Fix documentation",
        "body": "PR body",
        "html_url": "https://github.com/opentelekomcloud-docs/elastic-cloud-server/pull/400",
        "labels": [],
        "pull_request": {"url": "https://api.github.com/repos/.../pulls/400"},
        "state": "open"
    }


@pytest.fixture
def sample_github_comments():
    """Sample comments from GitHub issue."""
    return [
        {
            "user": {"login": "developer1"},
            "created_at": "2024-01-15T10:30:00Z",
            "body": "I can reproduce this issue."
        },
        {
            "user": {"login": "developer2"},
            "created_at": "2024-01-16T14:00:00Z",
            "body": "Here's a screenshot:\n![screenshot](https://example.com/img.png)"
        },
        {
            "user": {"login": "bot"},
            "created_at": "2024-01-17T09:00:00Z",
            "body": ""  # Empty comment, should be skipped
        }
    ]


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_CSV", "testdb")
    monkeypatch.setenv("DB_USER", "testuser")
    monkeypatch.setenv("DB_PASSWORD", "testpass")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    monkeypatch.setenv("GITHUB_ORGS", "opentelekomcloud-docs,opentelekomcloud-docs-swiss")
    monkeypatch.setenv("JIRA_API_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "jira_test_token")
    monkeypatch.setenv("JIRA_CERT_PATH", "/tmp/cert.pem")
    monkeypatch.setenv("JIRA_KEY_PATH", "/tmp/key.pem")
    monkeypatch.setenv("BASE_GITEA_URL", "https://gitea.example.com/api/v1")
