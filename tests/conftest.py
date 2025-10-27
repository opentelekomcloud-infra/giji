"""Test configuration for Giji."""

import pytest
from unittest.mock import MagicMock
import os

# Set test environment variables
os.environ.update({
    "GITHUB_BOT_TOKEN": "test-github-token",
    "JIRA_BOT_TOKEN": "test-jira-token",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "test_giji",
    "DB_USER": "test_user",
    "DB_PASSWORD": "test_password"
})


@pytest.fixture
def mock_github_client():
    """Mock GitHub client for testing."""
    return MagicMock()


@pytest.fixture
def mock_jira_client():
    """Mock Jira client for testing."""
    return MagicMock()


@pytest.fixture
def mock_database():
    """Mock database connection for testing."""
    return MagicMock()


@pytest.fixture
def sample_github_issue():
    """Sample GitHub issue data for testing."""
    return {
        "id": 12345,
        "number": 1,
        "title": "[BUG] Sample bug report",
        "body": "This is a sample bug report",
        "labels": [{"name": "bug"}],
        "html_url": "https://github.com/test/repo/issues/1",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z"
    }


@pytest.fixture
def sample_jira_issue():
    """Sample Jira issue data for testing."""
    return {
        "key": "BM-123",
        "fields": {
            "summary": "Sample bug report",
            "description": "This is a sample bug report",
            "issuetype": {"name": "Bug"},
            "project": {"key": "BM"}
        }
    }
