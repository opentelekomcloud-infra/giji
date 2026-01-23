"""Tests for config/connections.py"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEnvVariables:
    """Tests for EnvVariables class."""

    def test_github_orgs_parsing(self, mock_env_vars):
        """GITHUB_ORGS is parsed as comma-separated list."""
        from config.connections import EnvVariables

        env = EnvVariables()
        assert env.github_orgs == ["opentelekomcloud-docs", "opentelekomcloud-docs-swiss"]

    def test_single_org(self, monkeypatch):
        """Single org without comma works."""
        monkeypatch.setenv("DB_HOST", "localhost")
        monkeypatch.setenv("DB_PORT", "5432")
        monkeypatch.setenv("DB_CSV", "testdb")
        monkeypatch.setenv("DB_USER", "testuser")
        monkeypatch.setenv("DB_PASSWORD", "testpass")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
        monkeypatch.setenv("GITHUB_ORGS", "single-org")
        monkeypatch.setenv("JIRA_API_URL", "https://jira.example.com")
        monkeypatch.setenv("JIRA_TOKEN", "token")
        monkeypatch.setenv("JIRA_CERT_PATH", "/tmp/cert.pem")
        monkeypatch.setenv("JIRA_KEY_PATH", "/tmp/key.pem")
        monkeypatch.setenv("BASE_GITEA_URL", "https://gitea.example.com")

        from config.connections import EnvVariables
        env = EnvVariables()
        assert env.github_orgs == ["single-org"]

    def test_missing_env_var_raises(self, monkeypatch):
        """Missing required env var raises exception."""
        # Don't set any env vars
        monkeypatch.delenv("DB_HOST", raising=False)

        from config.connections import EnvVariables
        with pytest.raises(Exception, match="Missing environment variable"):
            EnvVariables()


class TestGitHubClient:
    """Tests for GitHubClient class."""

    @patch('config.connections.requests.get')
    def test_get_issues_success(self, mock_get, mock_env_vars):
        """Successful get_issues returns list of issues."""
        from config.connections import EnvVariables, GitHubClient

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"number": 1}, {"number": 2}]
        mock_get.return_value = mock_response

        env = EnvVariables()
        client = GitHubClient(env)
        issues = client.get_issues("test-org", "test-repo")

        assert len(issues) == 2
        assert issues[0]["number"] == 1

    @patch('config.connections.requests.get')
    def test_get_issues_failure(self, mock_get, mock_env_vars):
        """Failed get_issues raises RequestException."""
        from config.connections import EnvVariables, GitHubClient
        import requests

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_get.return_value = mock_response

        env = EnvVariables()
        client = GitHubClient(env)

        with pytest.raises(requests.RequestException):
            client.get_issues("test-org", "nonexistent-repo")

    @patch('config.connections.requests.get')
    def test_get_issue_comments(self, mock_get, mock_env_vars):
        """get_issue_comments returns list of comments."""
        from config.connections import EnvVariables, GitHubClient

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"body": "Comment 1"},
            {"body": "Comment 2"}
        ]
        mock_get.return_value = mock_response

        env = EnvVariables()
        client = GitHubClient(env)
        comments = client.get_issue_comments("test-org", "test-repo", 42)

        assert len(comments) == 2

    @patch('config.connections.requests.post')
    def test_add_label_success(self, mock_post, mock_env_vars):
        """Successful add_label returns True."""
        from config.connections import EnvVariables, GitHubClient

        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        env = EnvVariables()
        client = GitHubClient(env)
        result = client.add_label_to_issue("test-org", "test-repo", 42, ["bug"])

        assert result is True

    @patch('config.connections.requests.post')
    def test_add_comment_success(self, mock_post, mock_env_vars):
        """Successful add_comment returns True."""
        from config.connections import EnvVariables, GitHubClient

        mock_response = Mock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        env = EnvVariables()
        client = GitHubClient(env)
        result = client.add_comment_to_issue("test-org", "test-repo", 42, "Test comment")

        assert result is True


class TestJiraClient:
    """Tests for JiraClient class."""

    @patch('config.connections.requests.post')
    def test_create_issue_success(self, mock_post, mock_env_vars):
        """Successful create_issue returns issue data."""
        from config.connections import EnvVariables, JiraClient

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"key": "BM-123", "id": "12345"}
        mock_post.return_value = mock_response

        env = EnvVariables()
        client = JiraClient(env)
        result = client.create_issue({"fields": {"summary": "Test"}})

        assert result["key"] == "BM-123"

    @patch('config.connections.requests.post')
    def test_create_issue_failure(self, mock_post, mock_env_vars):
        """Failed create_issue returns None."""
        from config.connections import EnvVariables, JiraClient

        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response

        env = EnvVariables()
        client = JiraClient(env)
        result = client.create_issue({"fields": {"summary": "Test"}})

        assert result is None

    @patch('config.connections.requests.post')
    def test_check_issue_exists_true(self, mock_post, mock_env_vars):
        """check_issue_exists returns True when issue found."""
        from config.connections import EnvVariables, JiraClient

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"total": 1, "issues": [{"key": "BM-123"}]}
        mock_post.return_value = mock_response

        env = EnvVariables()
        client = JiraClient(env)
        result = client.check_issue_exists(42, "BM", "test-repo")

        assert result is True

    @patch('config.connections.requests.post')
    def test_check_issue_exists_false(self, mock_post, mock_env_vars):
        """check_issue_exists returns False when no issue found."""
        from config.connections import EnvVariables, JiraClient

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"total": 0, "issues": []}
        mock_post.return_value = mock_response

        env = EnvVariables()
        client = JiraClient(env)
        result = client.check_issue_exists(42, "BM", "test-repo")

        assert result is False

    @patch('config.connections.requests.post')
    def test_add_comment_success(self, mock_post, mock_env_vars):
        """Successful add_comment returns True."""
        from config.connections import EnvVariables, JiraClient

        mock_response = Mock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        env = EnvVariables()
        client = JiraClient(env)
        result = client.add_comment("BM-123", "Test comment")

        assert result is True


class TestGiteaClient:
    """Tests for GiteaClient class."""

    @patch('config.connections.requests.get')
    def test_list_directory(self, mock_get, mock_env_vars):
        """list_directory returns list of files."""
        from config.connections import EnvVariables, GiteaClient

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"name": "eu_de.yaml", "type": "file"},
            {"name": "eu_ch2.yaml", "type": "file"}
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        env = EnvVariables()
        client = GiteaClient(env)
        files = client.list_directory()

        assert len(files) == 2
        assert files[0]["name"] == "eu_de.yaml"

    @patch('config.connections.requests.get')
    def test_get_file_content(self, mock_get, mock_env_vars):
        """get_file_content returns decoded content."""
        import base64
        from config.connections import EnvVariables, GiteaClient

        yaml_content = "public_org: opentelekomcloud-docs\naffected_locations:\n  - EU-DE-01"
        encoded = base64.b64encode(yaml_content.encode()).decode()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"content": encoded}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        env = EnvVariables()
        client = GiteaClient(env)
        content = client.get_file_content("eu_de.yaml")

        assert "public_org: opentelekomcloud-docs" in content

    @patch('config.connections.requests.get')
    def test_get_affected_locations_for_org(self, mock_get, mock_env_vars):
        """get_affected_locations_for_org returns locations list."""
        import base64
        from config.connections import EnvVariables, GiteaClient

        # First call - list directory
        list_response = Mock()
        list_response.status_code = 200
        list_response.json.return_value = [{"name": "eu_de.yaml", "type": "file"}]
        list_response.raise_for_status = Mock()

        # Second call - get file content
        yaml_content = """public_org: opentelekomcloud-docs
affected_locations:
  - EU-DE-01 AZ1 (Germany/Biere)
  - EU-DE-02 AZ2 (Germany/Magdeburg)
"""
        encoded = base64.b64encode(yaml_content.encode()).decode()
        file_response = Mock()
        file_response.status_code = 200
        file_response.json.return_value = {"content": encoded}
        file_response.raise_for_status = Mock()

        mock_get.side_effect = [list_response, file_response]

        env = EnvVariables()
        client = GiteaClient(env)
        locations = client.get_affected_locations_for_org("opentelekomcloud-docs")

        assert len(locations) == 2
        assert "EU-DE-01 AZ1 (Germany/Biere)" in locations
