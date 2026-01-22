"""Module for bulk importing GitHub issues to Jira."""
import logging
import re
import time

import psycopg2

from config import Database, EnvVariables, GitHubClient, JiraClient, GiteaClient, Timer
from config import REPO_TO_MASTER_COMPONENT

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

IMPORTED_LABELS = ["imported-to-jira", "bulk"]
PROJECT_KEY = "BM"
ISSUE_TYPE = "Bug"
TARGET_SQUADS = ["Database Squad", "Compute Squad"]

HARDCODED_VALUES = {
    "bug_type": "Documentation",
    "affected_areas": "Production",
    "priority": "Medium",
    "test_category": "QA"
}

FALLBACK_AFFECTED_LOCATIONS = {
    "opentelekomcloud-docs": [
        "EU-DE-01 AZ1 (Germany/Biere)",
        "EU-DE-02 AZ2 (Germany/Magdeburg)",
        "EU-DE-03 AZ3 (Germany/Biere)"
    ],
    "opentelekomcloud-docs-swiss": [
        "EU-CH2-01 SwissCloud AZ1 (Switzerland/Zollikofen)",
        "EU-CH2-02 SwissCloud AZ2 (Switzerland/Bern)",
        "EU-CH2-03 SwissCloud AZ3 (Switzerland/Zollikofen)"
    ]
}

TEST_CATEGORY_IDS = {
    "QA": "17600",
    "UAT": "17601",
    "Security": "17602"
}


def get_affected_locations_for_org(org_name):
    """Get affected locations from Gitea with fallback."""
    locations = gitea_client.get_affected_locations_for_org(org_name)

    if locations:
        return locations

    # Fallback
    locations = FALLBACK_AFFECTED_LOCATIONS.get(org_name)
    if locations:
        return locations

    return ["EU-DE-03 AZ3 (Germany/Biere)"]


def get_repositories_from_db():
    """Get repositories from target squads in database."""
    conn = database.connect_to_db(env_vars.db_csv)
    repositories = []

    try:
        cur = conn.cursor()
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

        return repositories

    except psycopg2.Error as e:
        logger.error("Database query error: %s", e)
        raise
    finally:
        conn.close()


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

        time.sleep(1)

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

    template_field_map = {
        "master_component": "customfield_17001",
        "affected_locations": "customfield_10244",
        "test_category": "customfield_20100",
        "bug_type": "customfield_20101",
        "affected_areas": "customfield_10218",
        "users_impact": "customfield_25500"
    }

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

        # Affected locations from Gitea
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

            comment_body = f"Dear customer, this issue has been reported, ticket {jira_key}."
            github_client.add_comment_to_issue(github_org, repo_name, issue_number, comment_body)
            github_client.add_label_to_issue(github_org, repo_name, issue_number, IMPORTED_LABELS)

            successful_imports += 1
        else:
            failed_imports += 1

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

                    successful, failed, skipped = bulk_import_to_jira(issues, repo_name, github_org)

                    logger.info("%s/%s: Imported=%d, Failed=%d, Skipped=%d",
                               github_org, repo_name, successful, failed, skipped)

                    total_successful += successful
                    total_failed += failed
                    total_skipped += skipped

                except Exception as e:
                    logger.error("Error processing %s/%s: %s", github_org, repo_name, str(e))
                    continue

        logger.info("=" * 80)
        logger.info("FINAL SUMMARY: Imported=%d, Failed=%d, Skipped=%d",
                   total_successful, total_failed, total_skipped)
        logger.info("=" * 80)

    except Exception as e:
        logger.error("CRITICAL ERROR: %s", str(e), exc_info=True)


if __name__ == "__main__":
    main()
