"""Tests for demand_postgres.py"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDemandIssueDetection:
    """Tests for is_demand_issue function."""

    def test_demand_label(self, sample_demand_issue):
        """Issue with 'demand' label is detected."""
        from scripts.demand_postgres import is_demand_issue

        assert is_demand_issue(sample_demand_issue) is True

    def test_demand_prefix_in_title(self):
        """Issue with [DEMAND] prefix is detected."""
        from scripts.demand_postgres import is_demand_issue

        issue = {"title": "[DEMAND] New feature request", "labels": []}
        assert is_demand_issue(issue) is True

    def test_demand_prefix_lowercase(self):
        """[demand] prefix (lowercase) should also work."""
        from scripts.demand_postgres import is_demand_issue

        issue = {"title": "[demand] lowercase prefix", "labels": []}
        assert is_demand_issue(issue) is True

    def test_not_a_demand(self):
        """Issue without demand indicators is not a demand."""
        from scripts.demand_postgres import is_demand_issue

        issue = {"title": "Fix bug", "labels": [{"name": "bug"}]}
        assert is_demand_issue(issue) is False

    def test_bug_is_not_demand(self, sample_bug_issue):
        """Bug issue is not detected as demand."""
        from scripts.demand_postgres import is_demand_issue

        assert is_demand_issue(sample_bug_issue) is False


class TestParseDemandIssueBody:
    """Tests for parse_github_issue_body function in demand_postgres."""

    def test_parse_full_template(self, sample_demand_issue):
        """Parse demand issue with all template fields."""
        from scripts.demand_postgres import parse_github_issue_body

        fields = parse_github_issue_body(sample_demand_issue["body"])

        assert "summary" in fields
        assert "Resize API" in fields["summary"]
        assert "feature_description" in fields
        assert "change instance size" in fields["feature_description"]
        assert "doc_type" in fields
        assert "API" in fields["doc_type"]
        assert "UMN" in fields["doc_type"]

    def test_parse_empty_body(self):
        """Empty body returns empty dict."""
        from scripts.demand_postgres import parse_github_issue_body

        assert parse_github_issue_body("") == {}
        assert parse_github_issue_body(None) == {}

    def test_parse_doc_types_checkbox(self):
        """Parse checkbox-style document types."""
        from scripts.demand_postgres import parse_github_issue_body

        body = """### Documents Requested

- [x] API
- [ ] UMN
"""
        fields = parse_github_issue_body(body)
        assert "doc_type" in fields
        assert "API" in fields["doc_type"]
        assert "UMN" not in fields["doc_type"]

    def test_parse_both_doc_types_checked(self):
        """Parse when both doc types are checked."""
        from scripts.demand_postgres import parse_github_issue_body

        body = """### Documents Requested

- [x] API
- [x] UMN
"""
        fields = parse_github_issue_body(body)
        assert "doc_type" in fields
        assert "API" in fields["doc_type"]
        assert "UMN" in fields["doc_type"]
        assert len(fields["doc_type"]) == 2


class TestDemandImageConversion:
    """Tests for convert_github_images_to_jira in demand_postgres."""

    def test_convert_in_feature_description(self):
        """Images in feature description are converted."""
        from scripts.demand_postgres import convert_github_images_to_jira

        text = "Feature with ![diagram](https://example.com/diagram.png)"
        result = convert_github_images_to_jira(text)
        assert result == "Feature with !https://example.com/diagram.png!"

    def test_convert_in_additional_context(self):
        """Images in additional context are converted."""
        from scripts.demand_postgres import convert_github_images_to_jira

        text = 'See mockup: <img src="https://example.com/mockup.jpg">'
        result = convert_github_images_to_jira(text)
        assert result == "See mockup: !https://example.com/mockup.jpg!"


class TestDemandMasterComponent:
    """Tests for get_master_component_for_repo in demand_postgres."""

    def test_known_repo(self):
        """Known repository returns correct component."""
        from scripts.demand_postgres import get_master_component_for_repo

        mapping = {"geminidb": "OCH-1027721"}
        result = get_master_component_for_repo("geminidb", mapping)
        assert result == "OCH-1027721"

    def test_unknown_repo_uses_fallback(self):
        """Unknown repository uses fallback component."""
        from scripts.demand_postgres import get_master_component_for_repo
        from config import REPO_TO_MASTER_COMPONENT

        mapping = {}
        # Should return first available component as fallback
        result = get_master_component_for_repo("unknown-repo", mapping)
        assert result in REPO_TO_MASTER_COMPONENT.values()
