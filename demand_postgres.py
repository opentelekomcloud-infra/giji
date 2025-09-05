"""GitHub to JIRA Issue Importer for DEMAND - DATABASE VERSION"""
import logging
import os
import re

import psycopg2
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

IMPORTED_LABEL = "imported-to-jira"

PROJECT_KEY = "OTCPR"

ISSUE_TYPE_ID = "11001"

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD")
}

TARGET_SQUADS = ["Database Squad", "Compute Squad"]

REPO_TO_MASTER_COMPONENT = {
    "dedicated-host": "OCH-1027707",
    "auto-scaling": "OCH-1027753",
    "elastic-cloud-server": "OCH-1027712",
    "image-management-service": "OCH-1568488",
    "bare-metal-server": "OCH-1027668",
    "relational-database-service": "OCH-1027734",
    "gaussdb-opengauss": "OCH-1027718",
    "geminidb": "OCH-1027721",
    "gaussdb-mysql": "OCH-2332896",
    "data-replication-service": "OCH-1027709",
    "data-admin-service": "OCH-1027698",
    "distributed-database-middleware": "OCH-1278335",
    "document-database-service": "OCH-1027703"
}

HARDCODED_VALUES = {
    "estimated_effort": "15104",
    "pays_into": "15204",
    "priority": "Medium",
    "tier": "14637",
    "affected_locations": "EU-DE-03 AZ3 (Germany/Biere)"
}

TIMEOUT_SECONDS = 30


def connect_to_database():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        logger.info("Successfully connected to database")
        return conn
    except psycopg2.Error as e:
        logger.error("Error connecting to database: %s", e)
        raise


def get_repositories_from_db():
    conn = connect_to_database()
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
            logger.warning("No repositories found for squads: %s", TARGET_SQUADS)
            return []

        repo_component_mapping = {}

        for repository, squad, title in results:
            repositories.append(repository)
            logger.info("Found repository: %s (Squad: %s, Title: %s)", repository, squad, title)

            if repository in REPO_TO_MASTER_COMPONENT:
                repo_component_mapping[repository] = REPO_TO_MASTER_COMPONENT[repository]
            else:
                logger.warning("No master component mapping found for repository: %s", repository)

        logger.info("Found %s repositories from target squads", len(repositories))
        return repositories, repo_component_mapping

    except psycopg2.Error as e:
        logger.error("Error querying database: %s", e)
        raise
    finally:
        conn.close()


def is_demand_issue(issue):
    labels = [label["name"].lower() for label in issue.get("labels", [])]
    if "demand" in labels:
        return True

    title = issue.get("title", "").upper()
    if title.startswith("[DEMAND]"):
        return True

    return False


def get_jira_project_metadata(project_key):
    logger.info("Using minimal JIRA metadata for project: %s", project_key)

    return {
        'project_key': project_key,
        'issue_types': [{'name': 'Demand', 'id': '11001'}],
        'fields': {'description': {'name': 'description', 'required': False}}
    }


def export_github_issues(repo_name):
    logger.info("Fetching issues from repository: %s", repo_name)

    response = requests.get(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues",
        params={"state": "open"},
        headers=GITHUB_HEADERS,
        timeout=TIMEOUT_SECONDS
    )

    if response.status_code != 200:
        raise requests.RequestException(
            f"GitHub API request failed for {repo_name}: {response.status_code} {response.text}"
        )

    issues = response.json()
    logger.info("Found %s open issues in repository %s", len(issues), repo_name)
    return issues


def is_issue_already_imported(issue):
    labels = [label["name"] for label in issue.get("labels", [])]
    return IMPORTED_LABEL in labels


def add_imported_label(issue_number, repo_name):
    response = requests.post(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues/{issue_number}/labels",
        headers=GITHUB_HEADERS,
        json={"labels": [IMPORTED_LABEL]},
        timeout=TIMEOUT_SECONDS
    )

    if response.status_code != 200:
        logger.warning(
            "Failed to add 'imported-to-jira' label to issue #%s in %s: %s %s",
            issue_number, repo_name, response.status_code, response.text
        )
        return False

    logger.info("Added 'imported-to-jira' label to issue #%s in %s", issue_number, repo_name)
    return True


def add_jira_link_to_github_issue(issue_number, jira_key, repo_name):
    comment_body = f"This issue has been imported to Jira: [{jira_key}]({JIRA_URL}/browse/{jira_key})"

    response = requests.post(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues/{issue_number}/comments",
        headers=GITHUB_HEADERS,
        json={"body": comment_body},
        timeout=TIMEOUT_SECONDS
    )

    if response.status_code != 201:
        logger.warning(
            "Failed to add Jira link comment to GitHub issue #%s in %s: %s %s",
            issue_number, repo_name, response.status_code, response.text
        )
        return False

    logger.info("Added Jira link comment to GitHub issue #%s in %s", issue_number, repo_name)
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
        timeout=TIMEOUT_SECONDS
    )

    if response.status_code != 200:
        logger.warning(
            "Failed to search Jira for GitHub issue #%s in %s: %s %s",
            github_issue_number, repo_name, response.status_code, response.text
        )
        return False

    results = response.json()
    return results.get("total", 0) > 0


def parse_github_issue_body(issue_body):
    if not issue_body:
        return {}

    fields = {}

    summary_match = re.search(r'### Summary\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
    if summary_match:
        fields['summary'] = summary_match.group(1).strip()

    feature_description_match = re.search(r'### Feature Description\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body,
                                          re.DOTALL)
    if feature_description_match:
        fields['feature_description'] = feature_description_match.group(1).strip()

    doc_type_match = re.search(r'### Documents Requested\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
    if doc_type_match:
        doc_type_block = doc_type_match.group(1)
        doc_types = []
        for line in doc_type_block.strip().split('\n'):
            if line.strip().startswith('- [x]'):
                doc_type = re.search(r'- \[x\]\s*(.*)', line)
                if doc_type:
                    doc_types.append(doc_type.group(1).strip())
        fields['doc_type'] = doc_types

    return fields


def get_master_component_for_repo(repo_name, repo_component_mapping):
    component_key = repo_component_mapping.get(repo_name)
    if not component_key:
        logger.warning("No master component mapping found for repository: %s", repo_name)
        component_key = list(REPO_TO_MASTER_COMPONENT.values())[0]
        logger.warning("Using default master component: %s", component_key)

    return component_key


def import_to_jira(issues, repo_name, repo_component_mapping):
    successful_imports = 0
    failed_imports = 0
    skipped_imports = 0

    template_field_map = {
        "master_component": "customfield_17001",
        "affected_locations": "customfield_10244",
        "priority": "priority",
        "estimated_effort": "customfield_15700",
        "tier": "customfield_15237",
        "pays_into": "customfield_16000",
        "description": "description"
    }

    for issue in issues:
        issue_number = issue.get("number")

        if "pull_request" in issue:
            logger.info("Skipping PR #%s in %s", issue_number, repo_name)
            continue

        if not is_demand_issue(issue):
            logger.info("Skipping issue #%s in %s - not a demand issue", issue_number, repo_name)
            skipped_imports += 1
            continue

        if is_issue_already_imported(issue):
            logger.info("Skipping issue #%s in %s - already imported to Jira", issue_number, repo_name)
            skipped_imports += 1
            continue

        if check_jira_for_github_issue(issue_number, PROJECT_KEY, repo_name):
            logger.info("Skipping issue #%s in %s - found matching issue in Jira", issue_number, repo_name)
            add_imported_label(issue_number, repo_name)
            skipped_imports += 1
            continue

        template_fields = parse_github_issue_body(issue.get("body", ""))

        if not template_fields:
            logger.warning(
                "Issue #%s in %s does not appear to use the template format. Skipping.",
                issue_number, repo_name
            )
            skipped_imports += 1
            continue

        issue_data = {
            "fields": {
                "project": {"key": PROJECT_KEY},
                "issuetype": {"id": ISSUE_TYPE_ID},
                "summary": f"[{repo_name}] {issue.get('title', f'GitHub Issue #{issue_number}')}"
            }
        }

        master_component_key = get_master_component_for_repo(repo_name, repo_component_mapping)
        issue_data["fields"][template_field_map["master_component"]] = [{"key": master_component_key}]

        github_issue_url = issue.get('html_url')
        github_link_text = (f"\n\n*Imported from [GitHub Issue #{issue_number}]({github_issue_url}) in repository"
                            f" {repo_name}*")

        original_description = ""
        if 'feature_description' in template_fields:
            original_description = template_fields['feature_description']
        elif 'summary' in template_fields:
            original_description = template_fields['summary']
        else:
            original_description = issue.get("body", "")

        additional_info = ""

        if 'doc_type' in template_fields and template_fields['doc_type']:
            doc_types_str = ", ".join(template_fields['doc_type'])
            additional_info += f"Documents Requested:**\n{doc_types_str}"

        description_with_link = original_description + additional_info + github_link_text

        issue_data['fields']["description"] = description_with_link[:32767]

        issue_data["fields"][template_field_map["affected_locations"]] = [
            {"value": HARDCODED_VALUES["affected_locations"]}]

        issue_data["fields"]["priority"] = {"name": HARDCODED_VALUES["priority"]}
        issue_data["fields"][template_field_map["estimated_effort"]] = {"id": HARDCODED_VALUES["estimated_effort"]}
        issue_data["fields"][template_field_map["tier"]] = {"id": HARDCODED_VALUES["tier"]}
        issue_data["fields"][template_field_map["pays_into"]] = [{"id": HARDCODED_VALUES["pays_into"]}]

        issue_data["fields"]["labels"] = ["demand", "github-import", repo_name]

        logger.info(
            "Creating Jira issue for GitHub Issue #%s from %s: %s", issue_number, repo_name, issue['title'])

        response = requests.post(
            f"{JIRA_URL}/rest/api/2/issue",
            json=issue_data,
            headers=JIRA_HEADERS,
            timeout=TIMEOUT_SECONDS
        )

        if response.status_code == 201:
            jira_issue_key = response.json()["key"]
            logger.info(
                "Successfully created Jira issue: %s for GitHub Issue #%s from %s",
                jira_issue_key, issue_number, repo_name
            )

            add_jira_link_to_github_issue(issue_number, jira_issue_key, repo_name)

            add_imported_label(issue_number, repo_name)

            successful_imports += 1
        else:
            logger.error(
                "Failed to create Jira issue for GitHub Issue #%s from %s: %s %s",
                issue_number, repo_name, response.status_code, response.text
            )
            failed_imports += 1

    return successful_imports, failed_imports, skipped_imports


def check_environment_variables():
    missing_vars = []

    if not os.getenv("GITHUB_TOKEN"):
        missing_vars.append("GITHUB_TOKEN")

    if not os.getenv("JIRA_TOKEN_SANDBOX"):
        missing_vars.append("JIRA_TOKEN_SANDBOX")

    db_vars = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    for var in db_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        logger.error("Missing required environment variables: %s", ', '.join(missing_vars))
        logger.error("Please set these variables before running the script")
        return False

    logger.info("Environment variables: OK")
    return True


def main():
    logger.info("=" * 80)
    logger.info("GitHub to JIRA Issue Importer for DEMAND - DATABASE VERSION")
    logger.info("=" * 80)

    if not check_environment_variables():
        return

    try:
        logger.info("Fetching repositories from database...")
        repositories, repo_component_mapping = get_repositories_from_db()

        if not repositories:
            logger.error("No repositories found in database for target squads")
            return

        logger.info("Fetching JIRA metadata...")

        total_successful = 0
        total_failed = 0
        total_skipped = 0

        for repo_name in repositories:
            logger.info("Processing repository: %s", repo_name)
            master_component = repo_component_mapping.get(repo_name, 'NOT FOUND')
            logger.info("Master Component: %s", master_component)

            try:
                logger.info("Fetching issues from GitHub repository: %s...", repo_name)
                issues = export_github_issues(repo_name)

                if not issues:
                    logger.info("No issues found in repository %s, skipping...", repo_name)
                    continue

                logger.info("Importing demand issues from %s to JIRA...", repo_name)
                successful, failed, skipped = import_to_jira(issues, repo_name, repo_component_mapping)

                logger.info("Repository %s completed:", repo_name)
                logger.info("  Successfully imported: %s issues", successful)
                logger.info("  Failed to import: %s issues", failed)
                logger.info("  Skipped (not demands or already imported): %s issues", skipped)

                total_successful += successful
                total_failed += failed
                total_skipped += skipped

            except requests.RequestException as e:
                logger.error("ERROR processing repository %s: %s", repo_name, str(e))
                continue

        logger.info("FINAL SUMMARY - All repositories processed:")
        logger.info("  Total repositories processed: %s", len(repositories))
        logger.info("  Total successfully imported: %s issues", total_successful)
        logger.info("  Total failed to import: %s issues", total_failed)
        logger.info("  Total skipped: %s issues", total_skipped)

    except psycopg2.Error as e:
        logger.error("CRITICAL ERROR: %s", str(e), exc_info=True)


if __name__ == "__main__":
    main()
