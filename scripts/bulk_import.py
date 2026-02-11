"""Module for bulk importing GitHub issues to Jira."""
import logging
import os
import re
import time

from config.connections import Database, EnvVariables, GitHubClient, JiraClient, GiteaClient
from config.constants import REPO_TO_MASTER_COMPONENT, TEST_CATEGORY_IDS, template_field_map

env_vars = EnvVariables()
database = Database(env_vars)
github_client = GitHubClient(env_vars)
jira_client = JiraClient(env_vars)
gitea_client = GiteaClient(env_vars)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment variables (Vault)
IMPORTED_LABELS = ["imported-to-jira", "bulk"]
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "BM")
ISSUE_TYPE = os.getenv("JIRA_ISSUE_TYPE", "Bug")
TARGET_SQUADS = [s.strip() for s in os.getenv("TARGET_SQUADS", "Database Squad,Compute Squad").split(",")]


# Static values - these rarely change and don't need Vault
HARDCODED_VALUES = {
    "bug_type": "Documentation",
    "affected_areas": "Production",
    "priority": "Medium",
    "test_category": "QA"
}


def get_affected_locations_for_org(org_name):
    """Get affected locations from Gitea - no fallback, fail if unavailable."""
    locations = gitea_client.get_affected_locations_for_org(org_name)

    if not locations:
        raise RuntimeError(
            f"Failed to fetch affected locations for org '{org_name}' from Gitea. "
            "Gitea is required for operation - please ensure it is accessible."
        )

    return locations


def get_repositories_from_db():
    """Get repositories from target squads using context manager."""
    repositories = []

    # Context manager ensures proper connection cleanup
    with database.get_connection(env_vars.db_csv) as conn:
        try:
            with conn.cursor() as cur:
                query = """
                    SELECT "Repository", "Squad", "Title"
                    FROM repo_title_category
                    WHERE "Squad" IN %s
                    ORDER BY "Squad", "Repository"
                """

                cur.execute(query, (tuple(TARGET_SQUADS),))
                results = cur.fetchall()

                if not results:
                    return []

                for repository, squad, title in results:
                    repositories.append(repository)

        except Exception as e:
            logger.error("Error querying database: %s", e)
            raise

    return repositories


def convert_github_images_to_jira(text):
    """Convert GitHub image tags to Jira wiki format."""
    if not text:
        return text

    # HTML img tags
    pattern = r'<img[^>]+src="([^"]+)"[^>]*>'
    text = re.sub(pattern, lambda m: f"!{m.group(1)}!", text)

    # Markdown images
    markdown_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    text = re.sub(markdown_pattern, lambda m: f"!{m.group(2)}!", text)

    return text


def sync_comments_to_jira(jira_issue_key, github_org, repo_name, issue_number):
    """Sync GitHub comments to Jira issue."""
    comments = github_client.get_issue_comments(github_org, repo_name, issue_number)

    if not comments:
        return 0

    synced = 0
    for comment in comments:
        author = comment['user']['login']
        created = comment['created_at'][:10]
        body = comment.get('body', '')

        if not body:
            continue

        body_converted = convert_github_images_to_jira(body)
        comment_text = f"*Comment by {author} on {created}:*\n\n{body_converted}"

        if jira_client.add_comment(jira_issue_key, comment_text):
            synced += 1

        # Rate limiting delay to prevent GitHub API throttling
        time.sleep(0.5)

    return synced


def has_no_labels(issue):
    """Check if issue has no labels."""
    labels = issue.get("labels", [])
    return len(labels) == 0


def is_issue_already_imported(issue):
    """Check if already imported."""
    labels = [label["name"] for label in issue.get("labels", [])]
    return any(imported_label in labels for imported_label in IMPORTED_LABELS)


def get_master_component_for_repo(repo_name):
    """Get master component."""
    component_key = REPO_TO_MASTER_COMPONENT.get(repo_name)
    if not component_key:
        raise ValueError(f"Master component mapping missing for repository: {repo_name}")
    return component_key


def bulk_import_to_jira(issues, repo_name, github_org):
    """Bulk import issues."""
    successful_imports = 0
    failed_imports = 0
    skipped_imports = 0

    for issue in issues:
        issue_number = issue.get("number")

        if "pull_request" in issue:
            continue

        if not has_no_labels(issue):
            skipped_imports += 1
            continue

        if is_issue_already_imported(issue):
            skipped_imports += 1
            continue

        if jira_client.check_issue_exists(issue_number, PROJECT_KEY, repo_name):
            github_client.add_label_to_issue(github_org, repo_name, issue_number, IMPORTED_LABELS)
            skipped_imports += 1
            continue

        issue_data = {
            "fields": {
                "project": {"key": PROJECT_KEY},
                "issuetype": {"name": ISSUE_TYPE},
                "summary": f"[{repo_name}] {issue.get('title', f'GitHub Issue #{issue_number}')}"
            }
        }

        master_component_key = get_master_component_for_repo(repo_name)
        issue_data["fields"][template_field_map["master_component"]] = [{"key": master_component_key}]

        github_issue_url = issue.get('html_url')
        github_link_text = (f"\n\n*Bulk imported from [GitHub Issue #{issue_number}]({github_issue_url}) "
                            f"in repository {repo_name}*")

        issue_body = issue.get("body", "")
        if not issue_body:
            issue_body = "No description provided"

        # Convert images in body
        issue_body = convert_github_images_to_jira(issue_body)

        description_with_link = issue_body + github_link_text
        issue_data['fields']["description"] = description_with_link[:32767]

        issue_data["fields"][template_field_map["test_category"]] = {
            "id": TEST_CATEGORY_IDS[HARDCODED_VALUES["test_category"]]
        }

        # Affected locations from Gitea - will raise if unavailable
        affected_locations = get_affected_locations_for_org(github_org)
        issue_data["fields"][template_field_map["affected_locations"]] = [
            {"value": location} for location in affected_locations
        ]

        issue_data["fields"][template_field_map["bug_type"]] = [
            {"value": HARDCODED_VALUES["bug_type"]}
        ]

        issue_data["fields"][template_field_map["affected_areas"]] = [
            {"value": HARDCODED_VALUES["affected_areas"]}
        ]

        issue_data["fields"][template_field_map["users_impact"]] = "Not specified - bulk imported from unlabeled issue"

        issue_data["fields"]["priority"] = {"name": HARDCODED_VALUES["priority"]}

        issue_data["fields"]["labels"] = ["bulk-import", "github-import", repo_name]

        jira_issue = jira_client.create_issue(issue_data)

        if jira_issue:
            jira_key = jira_issue["key"]

            # Sync comments
            comment_count = sync_comments_to_jira(jira_key, github_org, repo_name, issue_number)
            if comment_count > 0:
                logger.info("Synced %d comments to %s", comment_count, jira_key)

            comment_body = f"This issue has been imported to Jira: {jira_key}"
            github_client.add_comment_to_issue(github_org, repo_name, issue_number, comment_body)
            github_client.add_label_to_issue(github_org, repo_name, issue_number, IMPORTED_LABELS)

            successful_imports += 1
        else:
            failed_imports += 1

        # Rate limiting delay to prevent GitHub API throttling
        time.sleep(0.5)

    return successful_imports, failed_imports, skipped_imports


def main():
    logger.info("=" * 80)
    logger.info("GitHub to JIRA BULK IMPORTER")
    logger.info("=" * 80)

    try:
        repositories = get_repositories_from_db()

        if not repositories:
            logger.error("No repositories found")
            return

        total_successful = 0
        total_failed = 0
        total_skipped = 0

        for github_org in env_vars.github_orgs:
            logger.info("Processing organization: %s", github_org)

            for repo_name in repositories:
                logger.info("Processing: %s/%s", github_org, repo_name)

                try:
                    issues = github_client.get_all_issues_paginated(github_org, repo_name)

                    if not issues:
                        continue

                    successful, failed, skipped = bulk_import_to_jira(
                        issues, repo_name, github_org)

                    logger.info(
                        "%s/%s: Imported=%d, Failed=%d, Skipped=%d",
                        github_org, repo_name, successful, failed, skipped)

                    total_successful += successful
                    total_failed += failed
                    total_skipped += skipped

                except Exception as e:
                    logger.error(
                        "Error processing %s/%s: %s", github_org, repo_name, str(e))
                    continue

        logger.info("=" * 80)
        logger.info(
            "FINAL SUMMARY: Imported=%d, Failed=%d, Skipped=%d",
            total_successful, total_failed, total_skipped)
        logger.info("=" * 80)

    except Exception as e:
        logger.error("CRITICAL ERROR: %s", str(e), exc_info=True)
    finally:
        # Ensure connection pool is properly closed
        database.close_pool()


if __name__ == "__main__":
    main()
