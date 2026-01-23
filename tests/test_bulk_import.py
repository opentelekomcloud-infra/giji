"""Tests for bulk_import.py"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHasNoLabels:
    """Tests for has_no_labels function."""

    def test_no_labels(self, sample_unlabeled_issue):
        """Issue without labels returns True."""
        from scripts.bulk_import import has_no_labels

        assert has_no_labels(sample_unlabeled_issue) is True

    def test_has_labels(self, sample_bug_issue):
        """Issue with labels returns False."""
        from scripts.bulk_import import has_no_labels

        assert has_no_labels(sample_bug_issue) is False

    def test_empty_labels_list(self):
        """Empty labels list returns True."""
        from scripts.bulk_import import has_no_labels

        issue = {"labels": []}
        assert has_no_labels(issue) is True

    def test_missing_labels_key(self):
        """Missing labels key returns True."""
        from scripts.bulk_import import has_no_labels

        issue = {}
        assert has_no_labels(issue) is True


class TestBulkImportedCheck:
    """Tests for is_issue_already_imported function in bulk_import."""

    def test_imported_with_bulk_label(self):
        """Issue with 'bulk' label is detected as imported."""
        from scripts.bulk_import import is_issue_already_imported

        issue = {"labels": [{"name": "bulk"}]}
        assert is_issue_already_imported(issue) is True

    def test_imported_with_imported_label(self):
        """Issue with 'imported-to-jira' label is detected as imported."""
        from scripts.bulk_import import is_issue_already_imported

        issue = {"labels": [{"name": "imported-to-jira"}]}
        assert is_issue_already_imported(issue) is True

    def test_imported_with_both_labels(self):
        """Issue with both labels is detected as imported."""
        from scripts.bulk_import import is_issue_already_imported

        issue = {"labels": [{"name": "bulk"}, {"name": "imported-to-jira"}]}
        assert is_issue_already_imported(issue) is True

    def test_not_imported(self):
        """Issue without import labels is not imported."""
        from scripts.bulk_import import is_issue_already_imported

        issue = {"labels": [{"name": "enhancement"}]}
        assert is_issue_already_imported(issue) is False


class TestBulkImageConversion:
    """Tests for convert_github_images_to_jira in bulk_import."""

    def test_github_user_attachments(self):
        """Convert GitHub user attachment URLs."""
        from scripts.bulk_import import convert_github_images_to_jira

        text = "![image](https://github.com/user-attachments/assets/abc123.png)"
        result = convert_github_images_to_jira(text)
        assert result == "!https://github.com/user-attachments/assets/abc123.png!"

    def test_preserve_text_around_images(self):
        """Text around images is preserved."""
        from scripts.bulk_import import convert_github_images_to_jira

        text = "Before ![img](https://url.com/img.png) after"
        result = convert_github_images_to_jira(text)
        assert result == "Before !https://url.com/img.png! after"


class TestBulkMasterComponent:
    """Tests for get_master_component_for_repo in bulk_import."""

    def test_known_repo(self):
        """Known repository returns correct component."""
        from scripts.bulk_import import get_master_component_for_repo

        result = get_master_component_for_repo("bare-metal-server")
        assert result == "OCH-1027668"

    def test_unknown_repo_raises(self):
        """Unknown repository raises ValueError."""
        from scripts.bulk_import import get_master_component_for_repo

        with pytest.raises(ValueError, match="Master component mapping missing"):
            get_master_component_for_repo("nonexistent-repo")


class TestPullRequestSkipping:
    """Tests for skipping pull requests."""

    def test_is_pull_request(self, sample_pull_request):
        """Pull request is identified by 'pull_request' key."""
        assert "pull_request" in sample_pull_request

    def test_issue_is_not_pr(self, sample_bug_issue):
        """Regular issue doesn't have 'pull_request' key."""
        assert "pull_request" not in sample_bug_issue
