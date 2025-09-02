import requests
import os
import re
import logging
import psycopg2

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

PROJECT_KEY = "BM"

ISSUE_TYPE = "Bug"

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT")
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
    "bug_type": "Documentation",
    "affected_areas": "Production",
    "priority": "Medium",
    "test_category": "QA",
    "affected_locations": "EU-DE-03 AZ3 (Germany/Biere)"
}


def connect_to_database():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        logger.info("Successfully connected to database")
        return conn
    except psycopg2.Error as e:
        logger.error(f"Error connecting to database: {e}")
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
            logger.warning(f"No repositories found for squads: {TARGET_SQUADS}")
            return []

        repo_component_mapping = {}

        for repository, squad, title in results:
            repositories.append(repository)
            logger.info(f"Found repository: {repository} (Squad: {squad}, Title: {title})")

            # Map repository to component based on existing mapping
            if repository in REPO_TO_MASTER_COMPONENT:
                repo_component_mapping[repository] = REPO_TO_MASTER_COMPONENT[repository]
            else:
                logger.warning(f"No master component mapping found for repository: {repository}")

        logger.info(f"Found {len(repositories)} repositories from target squads")
        return repositories, repo_component_mapping

    except psycopg2.Error as e:
        logger.error(f"Error querying database: {e}")
        raise
    finally:
        conn.close()


def is_bug_issue(issue):
    # Check labels for "bug"
    labels = [label["name"].lower() for label in issue.get("labels", [])]
    if "bug" in labels:
        return True

    title = issue.get("title", "").upper()
    if title.startswith("[BUG]"):
        return True

    return False


def get_jira_project_metadata(project_key):
    logger.info(f"Using minimal JIRA metadata for project: {project_key}")

    return {
        'project_key': project_key,
        'issue_types': [{'name': 'Bug', 'id': 'Bug'}],
        'fields': {'description': {'name': 'description', 'required': False}}
    }


def export_github_issues(repo_name):
    logger.info(f"📥 Fetching issues from repository: {repo_name}")

    response = requests.get(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues",
        params={"state": "open"},  # Only fetch open issues
        headers=GITHUB_HEADERS
    )

    if response.status_code != 200:
        raise Exception(f"GitHub API request failed for {repo_name}: {response.status_code} {response.text}")

    issues = response.json()
    logger.info(f"Found {len(issues)} open issues in repository {repo_name}")
    return issues


def is_issue_already_imported(issue):
    # Check if issue has the imported label
    labels = [label["name"] for label in issue.get("labels", [])]
    return IMPORTED_LABEL in labels


def add_imported_label(issue_number, repo_name):
    response = requests.post(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues/{issue_number}/labels",
        headers=GITHUB_HEADERS,
        json={"labels": [IMPORTED_LABEL]}
    )

    if response.status_code != 200:
        logger.warning(
            f"Failed to add 'imported-to-jira' label to issue #{issue_number} in {repo_name}: {response.status_code}"
            f" {response.text}")
        return False

    logger.info(f"Added 'imported-to-jira' label to issue #{issue_number} in {repo_name}")
    return True


def add_jira_link_to_github_issue(issue_number, jira_key, repo_name):
    comment_body = f"This issue has been imported to Jira: [{jira_key}]({JIRA_URL}/browse/{jira_key})"

    response = requests.post(
        f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/issues/{issue_number}/comments",
        headers=GITHUB_HEADERS,
        json={"body": comment_body}
    )

    if response.status_code != 201:
        logger.warning(
            f"Failed to add Jira link comment to GitHub issue #{issue_number} in {repo_name}: {response.status_code}"
            f" {response.text}")
        return False

    logger.info(f"Added Jira link comment to GitHub issue #{issue_number} in {repo_name}")
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
        }
    )

    if response.status_code != 200:
        logger.warning(
            f"Failed to search Jira for GitHub issue #{github_issue_number} in {repo_name}: {response.status_code}"
            f" {response.text}")
        return False

    results = response.json()
    return results.get("total", 0) > 0


def parse_github_issue_body(issue_body):
    if not issue_body:
        return {}

    fields = {}

    users_impact_match = re.search(r'### User\'s Impact\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
    if users_impact_match:
        fields['users_impact'] = users_impact_match.group(1).strip()

    url_match = re.search(r'### Document URL\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
    if url_match:
        fields['url'] = url_match.group(1).strip()

    description_match = re.search(r'### Description\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
    if description_match:
        fields['description'] = description_match.group(1).strip()

    additional_context_match = re.search(r'### Additional Context\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body,
                                         re.DOTALL)
    if additional_context_match:
        fields['additional_context'] = additional_context_match.group(1).strip()

    return fields


def get_master_component_for_repo(repo_name, repo_component_mapping):
    component_key = repo_component_mapping.get(repo_name)
    if not component_key:
        logger.error(f"No master component mapping found for repository: {repo_name}")
        raise Exception(f"Master component mapping missing for repository: {repo_name}")

    return component_key


def import_to_jira(issues, jira_metadata, repo_name, repo_component_mapping):
    successful_imports = 0
    failed_imports = 0
    skipped_imports = 0

    template_field_map = {
        "master_component": "customfield_17001",
        "users_impact": "customfield_24700",
        "affected_locations": "customfield_10244",
        "test_category": "customfield_20100",
        "priority": "priority",
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
            logger.info(f"Skipping PR #{issue_number} in {repo_name}")
            continue

        if not is_bug_issue(issue):
            logger.info(
                f"Skipping issue #{issue_number} in {repo_name} - not a bug issue (no 'bug' label or [BUG] prefix)")
            skipped_imports += 1
            continue

        logger.info(f"Processing BUG issue #{issue_number} from {repo_name} for import to project {PROJECT_KEY}")

        if is_issue_already_imported(issue):
            logger.info(f"Skipping issue #{issue_number} in {repo_name} - already imported to Jira")
            skipped_imports += 1
            continue

        if check_jira_for_github_issue(issue_number, PROJECT_KEY, repo_name):
            logger.info(f"Skipping issue #{issue_number} in {repo_name} - found matching issue in Jira")
            add_imported_label(issue_number, repo_name)
            skipped_imports += 1
            continue

        template_fields = parse_github_issue_body(issue.get("body", ""))

        if not template_fields:
            logger.warning(
                f"Issue #{issue_number} in {repo_name} does not appear to use the template format. Skipping.")
            skipped_imports += 1
            continue

        issue_data = {
            "fields": {
                "project": {"key": PROJECT_KEY},
                "issuetype": {"name": ISSUE_TYPE},
                "summary": f"[{repo_name}] {issue.get('title', f'GitHub Issue #{issue_number}')}"
            }
        }

        master_component_key = get_master_component_for_repo(repo_name, repo_component_mapping)
        issue_data["fields"][template_field_map["master_component"]] = [{"key": master_component_key}]

        github_issue_url = issue.get('html_url')
        github_link_text = (f"\n\n*Imported from [GitHub Issue #{issue_number}]({github_issue_url}) in repository"
                            f" {repo_name}*")

        original_description = ""
        if 'description' in template_fields:
            original_description = template_fields['description']
        else:
            original_description = issue.get("body", "")

        additional_info = ""

        if 'url' in template_fields and template_fields['url']:
            additional_info += f"Document URL:**\n{template_fields['url']}"

        if 'additional_context' in template_fields and template_fields['additional_context']:
            additional_info += f"Additional Context:**\n{template_fields['additional_context']}"

        description_with_link = original_description + additional_info + github_link_text

        issue_data['fields']["description"] = description_with_link[:32767]

        issue_data["fields"][template_field_map["test_category"]] = {
            "id": test_category_ids[HARDCODED_VALUES["test_category"]]}

        issue_data["fields"][template_field_map["affected_locations"]] = [
            {"value": HARDCODED_VALUES["affected_locations"]}]

        issue_data["fields"][template_field_map["bug_type"]] = [{"value": HARDCODED_VALUES["bug_type"]}]

        issue_data["fields"][template_field_map["affected_areas"]] = [{"value": HARDCODED_VALUES["affected_areas"]}]

        if 'users_impact' in template_fields:
            issue_data["fields"][template_field_map["users_impact"]] = template_fields['users_impact']

        issue_data["fields"]["priority"] = {"name": HARDCODED_VALUES["priority"]}

        issue_data["fields"]["labels"] = ["bug", "github-import", repo_name]

        logger.info(f"Creating Jira issue for GitHub Issue #{issue_number} from {repo_name}: {issue['title']}")

        response = requests.post(
            f"{JIRA_URL}/rest/api/2/issue",
            json=issue_data,
            headers=JIRA_HEADERS
        )

        if response.status_code == 201:
            jira_issue_key = response.json()["key"]
            logger.info(
                f"Successfully created Jira issue: {jira_issue_key} for GitHub Issue #{issue_number} from {repo_name}")

            add_jira_link_to_github_issue(issue_number, jira_issue_key, repo_name)

            add_imported_label(issue_number, repo_name)

            successful_imports += 1
        else:
            logger.error(
                f"Failed to create Jira issue for GitHub Issue #{issue_number} from {repo_name}: {response.status_code}"
                f" {response.text}")
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
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please set these variables before running the script")
        return False

    logger.info("Environment variables: OK")
    return True


def main():
    logger.info("=" * 80)
    logger.info("GitHub to JIRA Issue Importer for BUGS - MULTI REPOSITORY VERSION")
    logger.info("=" * 80)

    if not check_environment_variables():
        return

    try:
        logger.info("Fetching repositories from database...")
        repositories, repo_component_mapping = get_repositories_from_db()

        if not repositories:
            logger.error("No repositories found in database for target squads")
            return

        logger.info("Fetching JIRA metadata for BM project...")
        jira_metadata = get_jira_project_metadata(PROJECT_KEY)

        total_successful = 0
        total_failed = 0
        total_skipped = 0

        for repo_name in repositories:
            logger.info(f"Processing repository: {repo_name}")
            master_component = repo_component_mapping.get(repo_name, 'NOT FOUND')
            logger.info(f"Master Component: {master_component}")

            try:
                logger.info(f"Fetching issues from GitHub repository: {repo_name}...")
                issues = export_github_issues(repo_name)

                if not issues:
                    logger.info(f"No issues found in repository {repo_name}, skipping...")
                    continue

                logger.info(f"Importing bug issues from {repo_name} to JIRA...")
                successful, failed, skipped = import_to_jira(issues, jira_metadata, repo_name, repo_component_mapping)

                logger.info(f"Repository {repo_name} completed:")
                logger.info(f"  Successfully imported: {successful} issues")
                logger.info(f"  Failed to import: {failed} issues")
                logger.info(f"  Skipped (not bugs or already imported): {skipped} issues")

                total_successful += successful
                total_failed += failed
                total_skipped += skipped

            except Exception as e:
                logger.error(f"ERROR processing repository {repo_name}: {str(e)}")
                continue

        logger.info("FINAL SUMMARY - All repositories processed:")
        logger.info(f"  Total repositories processed: {len(repositories)}")
        logger.info(f"  Total successfully imported: {total_successful} issues")
        logger.info(f"  Total failed to import: {total_failed} issues")
        logger.info(f"  Total skipped: {total_skipped} issues")

    except Exception as e:
        logger.error(f"CRITICAL ERROR: {str(e)}", exc_info=True)


if __name__ == "__main__":
    main()
