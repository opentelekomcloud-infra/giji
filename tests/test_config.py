"""Test configuration module."""

import pytest
import os
from unittest.mock import patch
from giji.config import DatabaseConfig, GitHubConfig, JiraConfig, GijiConfig


class TestDatabaseConfig:
    """Test DatabaseConfig class."""

    def test_from_env_with_defaults(self):
        """Test DatabaseConfig.from_env() with default values."""
        with patch.dict(os.environ, {}, clear=True):
            config = DatabaseConfig.from_env()
            
            assert config.host == "localhost"
            assert config.port == 5432
            assert config.database == "giji"
            assert config.user == "giji"
            assert config.password == ""

    def test_from_env_with_custom_values(self):
        """Test DatabaseConfig.from_env() with custom environment variables."""
        env_vars = {
            "DB_HOST": "test-host",
            "DB_PORT": "5433",
            "DB_NAME": "test_db",
            "DB_USER": "test_user",
            "DB_PASSWORD": "test_pass"
        }
        
        with patch.dict(os.environ, env_vars, clear=True):
            config = DatabaseConfig.from_env()
            
            assert config.host == "test-host"
            assert config.port == 5433
            assert config.database == "test_db"
            assert config.user == "test_user"
            assert config.password == "test_pass"


class TestGitHubConfig:
    """Test GitHubConfig class."""

    def test_from_env_missing_token(self):
        """Test GitHubConfig.from_env() raises error when token is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="GITHUB_BOT_TOKEN environment variable is required"):
                GitHubConfig.from_env()

    def test_from_env_with_token_only(self):
        """Test GitHubConfig.from_env() with only token provided."""
        env_vars = {"GITHUB_BOT_TOKEN": "test-token"}
        
        with patch.dict(os.environ, env_vars, clear=True):
            config = GitHubConfig.from_env()
            
            assert config.token == "test-token"
            assert config.organization == "opentelekomcloud-docs"
            assert config.api_url == "https://api.github.com"

    def test_from_env_with_all_values(self):
        """Test GitHubConfig.from_env() with all environment variables."""
        env_vars = {
            "GITHUB_BOT_TOKEN": "test-token",
            "GITHUB_ORG": "test-org",
            "GITHUB_API_URL": "https://api.test.com"
        }
        
        with patch.dict(os.environ, env_vars, clear=True):
            config = GitHubConfig.from_env()
            
            assert config.token == "test-token"
            assert config.organization == "test-org"
            assert config.api_url == "https://api.test.com"


class TestJiraConfig:
    """Test JiraConfig class."""

    def test_from_env_missing_token(self):
        """Test JiraConfig.from_env() raises error when token is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="JIRA_BOT_TOKEN environment variable is required"):
                JiraConfig.from_env()

    def test_from_env_with_token_only(self):
        """Test JiraConfig.from_env() with only token provided."""
        env_vars = {"JIRA_BOT_TOKEN": "test-jira-token"}
        
        with patch.dict(os.environ, env_vars, clear=True):
            config = JiraConfig.from_env()
            
            assert config.token == "test-jira-token"
            assert config.url == "https://jira.tsi-dev.otc-service.com"
            assert config.project_key == "BM"

    def test_from_env_with_all_values(self):
        """Test JiraConfig.from_env() with all environment variables."""
        env_vars = {
            "JIRA_BOT_TOKEN": "test-jira-token",
            "JIRA_URL": "https://jira.test.com",
            "JIRA_PROJECT_KEY": "TEST"
        }
        
        with patch.dict(os.environ, env_vars, clear=True):
            config = JiraConfig.from_env()
            
            assert config.token == "test-jira-token"
            assert config.url == "https://jira.test.com"
            assert config.project_key == "TEST"


class TestGijiConfig:
    """Test GijiConfig class."""

    def test_from_env_success(self):
        """Test GijiConfig.from_env() with all required environment variables."""
        env_vars = {
            "GITHUB_BOT_TOKEN": "test-github-token",
            "JIRA_BOT_TOKEN": "test-jira-token",
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_NAME": "test_giji",
            "DB_USER": "test_user",
            "DB_PASSWORD": "test_password"
        }
        
        with patch.dict(os.environ, env_vars, clear=True):
            config = GijiConfig.from_env()
            
            # Test database config
            assert config.database.host == "localhost"
            assert config.database.database == "test_giji"
            
            # Test GitHub config
            assert config.github.token == "test-github-token"
            
            # Test Jira config
            assert config.jira.token == "test-jira-token"
            
            # Test default values
            assert config.target_squads == ["Database Squad", "Compute Squad"]
            assert config.imported_label == "imported-to-jira"
            assert config.log_level == "INFO"

    def test_from_env_with_custom_squads(self):
        """Test GijiConfig.from_env() with custom target squads."""
        env_vars = {
            "GITHUB_BOT_TOKEN": "test-github-token",
            "JIRA_BOT_TOKEN": "test-jira-token",
            "TARGET_SQUADS": "Squad A,Squad B,Squad C"
        }
        
        with patch.dict(os.environ, env_vars, clear=True):
            config = GijiConfig.from_env()
            
            assert config.target_squads == ["Squad A", "Squad B", "Squad C"]

    def test_from_file_not_implemented(self):
        """Test that from_file() raises NotImplementedError."""
        from pathlib import Path
        
        with pytest.raises(NotImplementedError, match="File-based configuration not yet implemented"):
            GijiConfig.from_file(Path("dummy.yaml"))