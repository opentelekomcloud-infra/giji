"""Module for bulk importing GitHub issues to Jira."""
import logging
import os
import time

import requests

GITHUB_ORG = "opentelekomcloud-docs"

GITHUB_API_URL = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_BOT_TOKEN")

JIRA_URL = "https://jira.tsi-dev.otc-service.com"
JIRA_API_TOKEN = os.getenv("JIRA_BOT_TOKEN")

GITHUB_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

JIRA_HEADERS = {
    "Authorization": f"Bearer {JIRA_API_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

IMPORTED_LABELS = ["imported-to-jira", "bulk"]

PROJECT_KEY = "BM"
ISSUE_TYPE = "Bug"

REPOSITORIES = [
    "dedicated-host",
    "auto-scaling",
    "elastic-cloud-server",
    "image-management-service",
    "bare-metal-server",
    "relational-database-service",
    "gaussdb-opengauss",
    "geminidb",
    "taurusdb",
    "gaussdb-mysql",
    "data-replication-service",
    "data-admin-service",
    "distributed-database-middleware",
    "document-database-service"
]

REPO_TO_MASTER_COMPONENT = {
    "dedicated-host": "OCH-1027707",
    "auto-scaling": "OCH-1027753",
    "elastic-cloud-server": "OCH-1027712",
    "image-management-service": "OCH-1568488",
    "bare-metal-server": "OCH-1027668",
    "relational-database-service": "OCH-1027734",
    "gaussdb-opengauss": "OCH-1027718",
    "geminidb": "OCH-1027721",
    "taurusdb": "OCH-2336898",
    "gaussdb-mysql": "OCH-2332896",
    "data-replication-service": "OCH-1027709",
    "data-admin-service": "OCH-1027698",
    "distributed-database-middleware": "OCH-1278335",
    "document-database-service": "OCH-1027703"
}

HARDCODED_VALUES = {
    "bug_type": "Documentation",
    "affected_areas": "Production",
    "priority": "Medium",
    "test_category": "QA",
    "affected_locations": "EU-DE-03 AZ3 (Germany/Biere)"
}


def export_all_github_issues(repo_name, state="open"):
    all_issues = []
    page = 1
    per_page = 100

    while True:
        response = requests.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues",
            params={
                "state": state,
                "per_page": per_page,
                "page": page
            },
            headers=GITHUB_HEADERS,
            timeout=30
        )

        if response.status_code != 200:
            raise ValueError(f"GitHub API request failed for {repo_name}: {response.status_code} {response.text}")

        issues = response.json()
        if not issues:
            break

        all_issues.extend(issues)
        if len(issues) < per_page:
            break

        page += 1
        time.sleep(0.1)

    logger.info("Found %d total issues in repository %s", len(all_issues), repo_name)
    return all_issues


def has_no_labels(issue):
    labels = issue.get("labels", [])
    return len(labels) == 0


def is_issue_already_imported(issue):
    labels = [label["name"] for label in issue.get("labels", [])]
    return any(imported_label in labels for imported_label in IMPORTED_LABELS)


def add_imported_label(issue_number, repo_name):
    response = requests.post(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues/{issue_number}/labels",
        headers=GITHUB_HEADERS,
        json={"labels": IMPORTED_LABELS},
        timeout=30
    )

    if response.status_code != 200:
        logger.warning(
            "Failed to add imported labels to issue #%d in %s: %d %s",
            issue_number, repo_name, response.status_code, response.text)
        return False

    logger.info("Added %s labels to issue #%d in %s", IMPORTED_LABELS, issue_number, repo_name)
    return True


def add_jira_link_to_github_issue(issue_number, jira_key, repo_name):
    comment_body = f"This issue has been imported to Jira via bulk import: [{jira_key}]({JIRA_URL}/browse/{jira_key})"

    response = requests.post(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues/{issue_number}/comments",
        headers=GITHUB_HEADERS,
        json={"body": comment_body},
        timeout=30
    )

    if response.status_code != 201:
        logger.warning(
            "Failed to add Jira link comment to GitHub issue #%d in %s: %d %s",
            issue_number, repo_name, response.status_code, response.text)
        return False

    logger.info("Added Jira link comment to GitHub issue #%d in %s", issue_number, repo_name)
    return True


def check_jira_for_github_issue(github_issue_number, project_key, repo_name):
    jql = f'project = {project_key} AND summary ~ "#{github_issue_number}" AND summary ~ "{repo_name}"'

    response = requests.post(
        f"{JIRA_URL}/rest/api/2/search",
        headers=JIRA_HEADERS,
        json={
            "jql": jql,
            "maxResults": 1,
            "fields": ["summary"]
        },
        timeout=30
    )

    if response.status_code != 200:
        logger.warning(
            "Failed to search Jira for GitHub issue #%d in %s: %d %s",
            github_issue_number, repo_name, response.status_code, response.text)
        return False

    results = response.json()
    return results.get("total", 0) > 0


def get_master_component_for_repo(repo_name):
    component_key = REPO_TO_MASTER_COMPONENT.get(repo_name)
    if not component_key:
        logger.error("No master component mapping found for repository: %s", repo_name)
        raise ValueError(f"Master component mapping missing for repository: {repo_name}")

    return component_key


def bulk_import_to_jira(issues, repo_name):
    successful_imports = 0
    failed_imports = 0
    skipped_imports = 0

    template_field_map = {
        "master_component": "customfield_17001",
        "affected_locations": "customfield_10244",
        "test_category": "customfield_20100",
        "bug_type": "customfield_20101",
        "affected_areas": "customfield_10218",
    }

    test_category_ids = {
        "QA": "17600",
        "UAT": "17601",
        "Security": "17602"
    }

    for issue in issues:
        issue_number = issue.get("number")

        if "pull_request" in issue:
            logger.info("Skipping PR #%d in %s", issue_number, repo_name)
            continue

        if not has_no_labels(issue):
            logger.info("Skipping issue #%d in %s - has labels (not bulk)", issue_number, repo_name)
            skipped_imports += 1
            continue

        if is_issue_already_imported(issue):
            logger.info("Skipping issue #%d in %s - already imported", issue_number, repo_name)
            skipped_imports += 1
            continue

        if check_jira_for_github_issue(issue_number, PROJECT_KEY, repo_name):
            logger.info("Skipping issue #%d in %s - found in Jira", issue_number, repo_name)
            add_imported_label(issue_number, repo_name)
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
        github_link_text = (f"\n\n*Bulk imported from [GitHub Issue #{issue_number}]({github_issue_url}) in repository"
                            f"{repo_name}*")

        issue_body = issue.get("body", "")
        if not issue_body:
            issue_body = "No description provided"

        description_with_link = issue_body + github_link_text
        issue_data['fields']["description"] = description_with_link[:32767]

        issue_data["fields"][template_field_map["test_category"]] = {
            "id": test_category_ids[HARDCODED_VALUES["test_category"]]
        }

        issue_data["fields"][template_field_map["affected_locations"]] = [
            {"value": HARDCODED_VALUES["affected_locations"]}
        ]

        issue_data["fields"][template_field_map["bug_type"]] = [
            {"value": HARDCODED_VALUES["bug_type"]}
        ]

        issue_data["fields"][template_field_map["affected_areas"]] = [
            {"value": HARDCODED_VALUES["affected_areas"]}
        ]

        issue_data["fields"]["priority"] = {"name": HARDCODED_VALUES["priority"]}

        issue_data["fields"]["labels"] = ["bulk-import", "github-import", repo_name]

        logger.info(
            "Creating Jira issue for GitHub Issue #%d from %s: %s", issue_number, repo_name, issue['title'])

        response = requests.post(
            f"{JIRA_URL}/rest/api/2/issue",
            json=issue_data,
            headers=JIRA_HEADERS,
            timeout=30
        )

        if response.status_code == 201:
            jira_issue_key = response.json()["key"]
            logger.info(
                "Successfully created Jira issue: %s for GitHub Issue #%d", jira_issue_key, issue_number)

            add_jira_link_to_github_issue(issue_number, jira_issue_key, repo_name)
            add_imported_label(issue_number, repo_name)

            successful_imports += 1
        else:
            logger.error(
                "Failed create Jira issue for #%d: %d %s", issue_number, response.status_code, response.text)
            failed_imports += 1

        time.sleep(0.5)

    return successful_imports, failed_imports, skipped_imports


def check_environment_variables():
    missing_vars = []

    if not os.getenv("GITHUB_TOKEN"):
        missing_vars.append("GITHUB_TOKEN")

    if not os.getenv("JIRA_TOKEN_SANDBOX"):
        missing_vars.append("JIRA_TOKEN_SANDBOX")

    if missing_vars:
        logger.error("Missing required environment variables: %s", ', '.join(missing_vars))
        return False

    logger.info("Environment variables: OK")
    return True


def main():
    logger.info("=" * 80)
    logger.info("GitHub to JIRA BULK IMPORTER - ISSUES WITH NO LABELS")
    logger.info("=" * 80)

    if not check_environment_variables():
        return

    try:
        repositories = REPOSITORIES
        logger.info("Found %d repositories to process", len(repositories))

        total_successful = 0
        total_failed = 0
        total_skipped = 0

        for repo_name in repositories:
            logger.info("\nProcessing repository: %s", repo_name)

            try:
                issues = export_all_github_issues(repo_name, state="open")

                if not issues:
                    logger.info("No issues found in repository %s", repo_name)
                    continue

                logger.info("Bulk importing issues with no labels from %s...", repo_name)
                successful, failed, skipped = bulk_import_to_jira(issues, repo_name)

                logger.info("Repository %s completed:", repo_name)
                logger.info("  Successfully imported: %d issues", successful)
                logger.info("  Failed to import: %d issues", failed)
                logger.info("  Skipped (has labels or already imported): %d issues", skipped)

                total_successful += successful
                total_failed += failed
                total_skipped += skipped

            except (ValueError, requests.RequestException) as e:
                logger.error("ERROR processing repository %s: %s", repo_name, str(e))
                continue

        logger.info("\nFINAL SUMMARY - Bulk Import Completed:")
        logger.info("  Total repositories processed: %d", len(repositories))
        logger.info("  Total successfully imported: %d issues", total_successful)
        logger.info("  Total failed to import: %d issues", total_failed)
        logger.info("  Total skipped: %d issues", total_skipped)

    except (ValueError, requests.RequestException) as e:
        logger.error("CRITICAL ERROR: %s", str(e), exc_info=True)


if __name__ == "__main__":
    main()
