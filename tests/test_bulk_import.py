"""Test bulk import functionality with mocking."""

import pytest
import os
import sys
from unittest.mock import patch, MagicMock, Mock


class TestBulkImportFunctions:
    """Test bulk import functions with mocked dependencies."""

    def setup_method(self):
        """Set up test environment variables."""
        self.test_env_vars = {
            "GITHUB_BOT_TOKEN": "test-github-token",
            "JIRA_BOT_TOKEN": "test-jira-token",
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_NAME": "test_giji",
            "DB_USER": "test_user",
            "DB_PASSWORD": "test_password"
        }

    @patch.dict('sys.modules', {'psycopg2': MagicMock()})
    @patch.dict(os.environ, {
        "GITHUB_TOKEN": "test-token",
        "JIRA_TOKEN_SANDBOX": "test-jira-token",
        "DB_HOST": "localhost",
        "DB_NAME": "test_db",
        "DB_USER": "test_user",
        "DB_PASSWORD": "test_pass"
    })
    def test_check_environment_variables(self):
        """Test environment variable checking function."""
        import giji.bulk_import
        
        # This should not raise an exception with mocked env vars
        result = giji.bulk_import.check_environment_variables()
        assert result is True  # Function returns True on success

    @patch.dict('sys.modules', {'psycopg2': MagicMock()})
    def test_has_no_labels(self):
        """Test the has_no_labels function."""
        import giji.bulk_import
        
        # Test issue with no labels
        issue_no_labels = {"labels": []}
        assert giji.bulk_import.has_no_labels(issue_no_labels) is True
        
        # Test issue with labels
        issue_with_labels = {"labels": [{"name": "bug"}]}
        assert giji.bulk_import.has_no_labels(issue_with_labels) is False

    @patch.dict('sys.modules', {'psycopg2': MagicMock()})
    def test_is_issue_already_imported(self):
        """Test the is_issue_already_imported function."""
        import giji.bulk_import
        
        # Test issue not imported
        issue_not_imported = {"labels": [{"name": "bug"}]}
        assert giji.bulk_import.is_issue_already_imported(issue_not_imported) is False
        
        # Test issue already imported
        issue_imported = {"labels": [{"name": "imported-to-jira"}]}
        assert giji.bulk_import.is_issue_already_imported(issue_imported) is True

    @patch.dict('sys.modules', {'psycopg2': MagicMock()})
    @patch('giji.bulk_import.requests.get')
    def test_export_all_github_issues_with_mock(self, mock_get):
        """Test exporting GitHub issues with mocked requests."""
        import giji.bulk_import
        
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": 1,
                "number": 1,
                "title": "Test Issue",
                "body": "Test body",
                "labels": [],
                "state": "open"
            }
        ]
        mock_get.return_value = mock_response
        
        result = giji.bulk_import.export_all_github_issues("test-repo")
        
        # Should return the mocked issue
        assert len(result) == 1
        assert result[0]["title"] == "Test Issue"
        
        # Verify the API was called correctly
        mock_get.assert_called()

    @patch.dict('sys.modules', {'psycopg2': MagicMock()})
    @patch('giji.bulk_import.requests.post')
    def test_add_imported_label_with_mock(self, mock_post):
        """Test adding imported label with mocked requests."""
        import giji.bulk_import
        
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "success"}
        mock_post.return_value = mock_response
        
        result = giji.bulk_import.add_imported_label(1, "test-repo")
        
        # Should return True for successful operation
        assert result is True
        
        # Verify the API was called
        mock_post.assert_called_once()


class TestBulkImportIntegration:
    """Test bulk import module integration."""

    @patch.dict('sys.modules', {'psycopg2': MagicMock()})
    @patch.dict(os.environ, {
        "GITHUB_BOT_TOKEN": "test-token",
        "JIRA_BOT_TOKEN": "test-jira-token",
        "DB_HOST": "localhost"
    })
    def test_module_imports_and_constants(self):
        """Test that module imports correctly and has expected constants."""
        import giji.bulk_import
        
        # Test constants
        assert giji.bulk_import.GITHUB_ORG == "opentelekomcloud-docs"
        assert giji.bulk_import.GITHUB_API_URL == "https://api.github.com"
        assert giji.bulk_import.JIRA_URL == "https://jira.tsi-dev.otc-service.com"
        assert giji.bulk_import.PROJECT_KEY == "BM"
        assert giji.bulk_import.ISSUE_TYPE == "Bug"
        
        # Test that imported labels exist
        assert "imported-to-jira" in giji.bulk_import.IMPORTED_LABELS
        assert "bulk" in giji.bulk_import.IMPORTED_LABELS

    @patch.dict('sys.modules', {'psycopg2': MagicMock()})
    def test_module_functions_exist(self):
        """Test that expected functions exist in the module."""
        import giji.bulk_import
        
        # Check that key functions exist and are callable
        assert callable(giji.bulk_import.main)
        assert callable(giji.bulk_import.bulk_import_to_jira)
        assert callable(giji.bulk_import.export_all_github_issues)
        assert callable(giji.bulk_import.connect_to_database)
        assert callable(giji.bulk_import.check_environment_variables)