"""Tests for bug_postgres.py"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestImageConversion:
    """Tests for convert_github_images_to_jira function."""

    def test_convert_markdown_image(self):
        """Convert markdown image syntax to Jira format."""
        from scripts.bug_postgres import convert_github_images_to_jira

        text = "Check this: ![screenshot](https://example.com/image.png)"
        result = convert_github_images_to_jira(text)
        assert result == "Check this: !https://example.com/image.png!"

    def test_convert_html_img_tag(self):
        """Convert HTML img tag to Jira format."""
        from scripts.bug_postgres import convert_github_images_to_jira

        text = 'See: <img src="https://example.com/pic.jpg" width="500">'
        result = convert_github_images_to_jira(text)
        assert result == "See: !https://example.com/pic.jpg!"

    def test_convert_multiple_images(self):
        """Convert multiple images in same text."""
        from scripts.bug_postgres import convert_github_images_to_jira

        text = "First ![a](https://a.com/1.png) and second ![b](https://b.com/2.png)"
        result = convert_github_images_to_jira(text)
        assert result == "First !https://a.com/1.png! and second !https://b.com/2.png!"

    def test_convert_mixed_formats(self):
        """Convert mixed markdown and HTML images."""
        from scripts.bug_postgres import convert_github_images_to_jira

        text = '![md](https://md.com/img.png) and <img src="https://html.com/img.jpg">'
        result = convert_github_images_to_jira(text)
        assert "!https://md.com/img.png!" in result
        assert "!https://html.com/img.jpg!" in result

    def test_no_images(self):
        """Text without images remains unchanged."""
        from scripts.bug_postgres import convert_github_images_to_jira

        text = "Just plain text without any images."
        result = convert_github_images_to_jira(text)
        assert result == text

    def test_none_input(self):
        """None input returns None."""
        from scripts.bug_postgres import convert_github_images_to_jira

        result = convert_github_images_to_jira(None)
        assert result is None

    def test_empty_string(self):
        """Empty string returns empty string."""
        from scripts.bug_postgres import convert_github_images_to_jira

        result = convert_github_images_to_jira("")
        assert result == ""


class TestIssueTypeDetection:
    """Tests for is_bug_issue function."""

    def test_bug_label(self, sample_bug_issue):
        """Issue with 'bug' label is detected as bug."""
        from scripts.bug_postgres import is_bug_issue

        assert is_bug_issue(sample_bug_issue) is True

    def test_bug_prefix_in_title(self):
        """Issue with [BUG] prefix is detected as bug."""
        from scripts.bug_postgres import is_bug_issue

        issue = {"title": "[BUG] Something broken", "labels": []}
        assert is_bug_issue(issue) is True

    def test_bug_prefix_lowercase(self):
        """[bug] prefix (lowercase) should also work."""
        from scripts.bug_postgres import is_bug_issue

        issue = {"title": "[bug] lowercase prefix", "labels": []}
        # Title is uppercased in function, so this should work
        assert is_bug_issue(issue) is True

    def test_not_a_bug(self):
        """Issue without bug indicators is not a bug."""
        from scripts.bug_postgres import is_bug_issue

        issue = {"title": "Add new feature", "labels": [{"name": "enhancement"}]}
        assert is_bug_issue(issue) is False

    def test_demand_is_not_bug(self, sample_demand_issue):
        """Demand issue is not detected as bug."""
        from scripts.bug_postgres import is_bug_issue

        assert is_bug_issue(sample_demand_issue) is False


class TestImportedCheck:
    """Tests for is_issue_already_imported function."""

    def test_imported_issue(self, sample_imported_issue):
        """Issue with 'imported-to-jira' label is detected."""
        from scripts.bug_postgres import is_issue_already_imported

        assert is_issue_already_imported(sample_imported_issue) is True

    def test_not_imported(self, sample_bug_issue):
        """Issue without import label is not imported."""
        from scripts.bug_postgres import is_issue_already_imported

        assert is_issue_already_imported(sample_bug_issue) is False

    def test_no_labels(self, sample_unlabeled_issue):
        """Issue without labels is not imported."""
        from scripts.bug_postgres import is_issue_already_imported

        assert is_issue_already_imported(sample_unlabeled_issue) is False


class TestParseGithubIssueBody:
    """Tests for parse_github_issue_body function."""

    def test_parse_full_template(self, sample_bug_issue):
        """Parse issue with all template fields."""
        from scripts.bug_postgres import parse_github_issue_body

        fields = parse_github_issue_body(sample_bug_issue["body"])

        assert "url" in fields
        assert "ecs_api_0001.html" in fields["url"]
        assert "description" in fields
        assert "wrong format" in fields["description"]
        assert "users_impact" in fields
        assert "cannot understand" in fields["users_impact"]
        assert "additional_context" in fields
        assert "Screenshot" in fields["additional_context"]

    def test_parse_empty_body(self):
        """Empty body returns empty dict."""
        from scripts.bug_postgres import parse_github_issue_body

        assert parse_github_issue_body("") == {}
        assert parse_github_issue_body(None) == {}

    def test_parse_no_template(self, sample_bug_issue_no_template):
        """Body without template returns empty dict."""
        from scripts.bug_postgres import parse_github_issue_body

        fields = parse_github_issue_body(sample_bug_issue_no_template["body"])
        # No ### sections, so no fields extracted
        assert fields == {}


class TestTestCategoryFromUrl:
    """Tests for determine_test_category_from_url function."""

    def test_umn_url(self):
        """UMN URL returns UAT category."""
        from scripts.bug_postgres import determine_test_category_from_url

        url = "https://docs.otc.t-systems.com/umn/ecs/topic.html"
        assert determine_test_category_from_url(url) == "UAT"

    def test_api_ref_url(self):
        """API reference URL returns QA category."""
        from scripts.bug_postgres import determine_test_category_from_url

        url = "https://docs.otc.t-systems.com/api-ref/ecs/api.html"
        assert determine_test_category_from_url(url) == "QA"

    def test_other_url(self):
        """Other URLs default to QA."""
        from scripts.bug_postgres import determine_test_category_from_url

        url = "https://docs.otc.t-systems.com/other/page.html"
        assert determine_test_category_from_url(url) == "QA"

    def test_empty_url(self):
        """Empty/None URL returns QA."""
        from scripts.bug_postgres import determine_test_category_from_url

        assert determine_test_category_from_url("") == "QA"
        assert determine_test_category_from_url(None) == "QA"


class TestMasterComponent:
    """Tests for get_master_component_for_repo function."""

    def test_known_repo(self):
        """Known repository returns correct component."""
        from scripts.bug_postgres import get_master_component_for_repo

        mapping = {"elastic-cloud-server": "OCH-1027712"}
        result = get_master_component_for_repo("elastic-cloud-server", mapping)
        assert result == "OCH-1027712"

    def test_unknown_repo(self):
        """Unknown repository raises ValueError."""
        from scripts.bug_postgres import get_master_component_for_repo

        mapping = {"elastic-cloud-server": "OCH-1027712"}
        with pytest.raises(ValueError, match="Master component mapping missing"):
            get_master_component_for_repo("unknown-repo", mapping)
