"""Test main module."""

import pytest
import sys
from unittest.mock import patch, MagicMock
from giji import __version__


class TestVersion:
    """Test version information."""

    def test_version_exists(self):
        """Test that version is defined."""
        assert __version__ == "0.1.0"


class TestModuleImports:
    """Test module imports work without dependencies."""

    def test_config_import(self):
        """Test that config module can be imported."""
        from giji.config import GijiConfig
        assert GijiConfig is not None

    def test_config_classes_exist(self):
        """Test that config classes exist."""
        from giji.config import DatabaseConfig, GitHubConfig, JiraConfig, GijiConfig
        
        assert DatabaseConfig is not None
        assert GitHubConfig is not None
        assert JiraConfig is not None
        assert GijiConfig is not None

    def test_bug_postgres_import_with_mock(self):
        """Test that bug_postgres module can be imported with mocked dependencies."""
        # Mock psycopg2 in sys.modules before importing
        with patch.dict('sys.modules', {'psycopg2': MagicMock()}):
            import giji.bug_postgres
            assert giji.bug_postgres.GITHUB_ORG == "opentelekomcloud-docs"
            assert giji.bug_postgres.GITHUB_API_URL == "https://api.github.com"
            assert giji.bug_postgres.JIRA_URL == "https://jira.tsi-dev.otc-service.com"

    def test_bulk_import_import_with_mock(self):
        """Test that bulk_import module can be imported with mocked dependencies."""
        # Mock psycopg2 in sys.modules before importing
        with patch.dict('sys.modules', {'psycopg2': MagicMock()}):
            import giji.bulk_import
            assert hasattr(giji.bulk_import, 'main')
            assert hasattr(giji.bulk_import, 'bulk_import_to_jira')

    def test_demand_postgres_import_with_mock(self):
        """Test that demand_postgres module can be imported with mocked dependencies."""
        # Mock psycopg2 in sys.modules before importing
        with patch.dict('sys.modules', {'psycopg2': MagicMock()}):
            import giji.demand_postgres
            assert callable(getattr(giji.demand_postgres, 'main', None))

    def test_templates_distribution_import_with_mock(self):
        """Test that templates_distribution module can be imported with mocked dependencies."""
        # Mock psycopg2 in sys.modules before importing
        with patch.dict('sys.modules', {'psycopg2': MagicMock()}):
            import giji.templates_distribution
            assert callable(getattr(giji.templates_distribution, 'main', None))