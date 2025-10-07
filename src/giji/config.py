"""Configuration management for Giji application."""

import os
from typing import List, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DatabaseConfig:
    """Database connection configuration."""
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> 'DatabaseConfig':
        """Create database config from environment variables."""
        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "giji"),
            user=os.getenv("DB_USER", "giji"),
            password=os.getenv("DB_PASSWORD", "")
        )


@dataclass
class GitHubConfig:
    """GitHub API configuration."""
    token: str
    organization: str = "opentelekomcloud-docs"
    api_url: str = "https://api.github.com"

    @classmethod
    def from_env(cls) -> 'GitHubConfig':
        """Create GitHub config from environment variables."""
        token = os.getenv("GITHUB_BOT_TOKEN")
        if not token:
            raise ValueError("GITHUB_BOT_TOKEN environment variable is required")

        return cls(
            token=token,
            organization=os.getenv("GITHUB_ORG", cls.organization),
            api_url=os.getenv("GITHUB_API_URL", cls.api_url)
        )


@dataclass
class JiraConfig:
    """Jira API configuration."""
    token: str
    url: str = "https://jira.tsi-dev.otc-service.com"
    project_key: str = "BM"

    @classmethod
    def from_env(cls) -> 'JiraConfig':
        """Create Jira config from environment variables."""
        token = os.getenv("JIRA_BOT_TOKEN")
        if not token:
            raise ValueError("JIRA_BOT_TOKEN environment variable is required")

        return cls(
            token=token,
            url=os.getenv("JIRA_URL", cls.url),
            project_key=os.getenv("JIRA_PROJECT_KEY", cls.project_key)
        )


@dataclass
class GijiConfig:
    """Main application configuration."""
    database: DatabaseConfig
    github: GitHubConfig
    jira: JiraConfig
    target_squads: List[str]
    imported_label: str = "imported-to-jira"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> 'GijiConfig':
        """Create application config from environment variables."""
        return cls(
            database=DatabaseConfig.from_env(),
            github=GitHubConfig.from_env(),
            jira=JiraConfig.from_env(),
            target_squads=os.getenv("TARGET_SQUADS", "Database Squad,Compute Squad").split(","),
            imported_label=os.getenv("IMPORTED_LABEL", cls.imported_label),
            log_level=os.getenv("LOG_LEVEL", cls.log_level)
        )

    @classmethod
    def from_file(cls, config_path: Path) -> 'GijiConfig':
        """Load configuration from a file (for future YAML/TOML support)."""
        # Placeholder for future file-based configuration
        raise NotImplementedError("File-based configuration not yet implemented")
