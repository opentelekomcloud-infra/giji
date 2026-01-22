"""GitHub to JIRA Issue Importer for DEMAND"""
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

IMPORTED_LABEL = "imported-to-jira"
PROJECT_KEY = "OTCPR"
ISSUE_TYPE_ID = "11001"
TARGET_SQUADS = ["Database Squad", "Compute Squad"]

HARDCODED_VALUES = {
    "estimated_effort": "15104",
    "pays_into": "15204",
    "priority": "Medium",
    "tier": "14637"
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
    repo_component_mapping = {}

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
            return [], {}

        for repository, squad, title in results:
            repositories.append(repository)
            if repository in REPO_TO_MASTER_COMPONENT:
                repo_component_mapping[repository] = REPO_TO_MASTER_COMPONENT[repository]

        return repositories, repo_component_mapping

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

        time.sleep(0.5)

    return synced


def is_demand_issue(issue):
    """Check if issue is a demand."""
    labels = [label["name"].lower() for label in issue.get("labels", [])]
    if "demand" in labels:
        return True

    title = issue.get("title", "").upper()
    if title.startswith("[DEMAND]"):
        return True

    return False


def is_issue_already_imported(issue):
    """Check if issue has imported label."""
    labels = [label["name"] for label in issue.get("labels", [])]
    return IMPORTED_LABEL in labels


def parse_github_issue_body(issue_body):
    """Parse GitHub issue template fields."""
    if not issue_body:
        return {}

    fields = {}

    summary_match = re.search(r'### Summary\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
    if summary_match:
        fields['summary'] = summary_match.group(1).strip()

    feature_description_match = re.search(r'### Feature Description\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
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

    additional_context_match = re.search(r'### Additional Context\s*\n\s*([\s\S]*?)(?:\n\s*###|$)', issue_body, re.DOTALL)
    if additional_context_match:
        fields['additional_context'] = additional_context_match.group(1).strip()

    return fields


def get_master_component_for_repo(repo_name, repo_component_mapping):
    """Get master component key for repository."""
    component_key = repo_component_mapping.get(repo_name)
    if not component_key:
        # Fallback to first available component
        component_key = list(REPO_TO_MASTER_COMPONENT.values())[0]
    return component_key


def import_to_jira(issues, repo_name, repo_component_mapping, github_org):
    """Import GitHub issues to Jira."""
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
            continue

        if not is_demand_issue(issue):
            skipped_imports += 1
            continue

        if is_issue_already_imported(issue):
            skipped_imports += 1
            continue

        if jira_client.check_issue_exists(issue_number, PROJECT_KEY, repo_name):
            github_client.add_label_to_issue(github_org, repo_name, issue_number, [IMPORTED_LABEL])
            skipped_imports += 1
            continue

        template_fields = parse_github_issue_body(issue.get("body", ""))

        if not template_fields:
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
        github_link_text = (f"\n\n*Imported from [GitHub Issue #{issue_number}]({github_issue_url}) "
                            f"in repository {repo_name}*")

        # Description with image conversion
        if 'feature_description' in template_fields:
            original_description = template_fields['feature_description']
        elif 'summary' in template_fields:
            original_description = template_fields['summary']
        else:
            original_description = issue.get("body", "")

        original_description = convert_github_images_to_jira(original_description)

        additional_info = ""
        if 'doc_type' in template_fields and template_fields['doc_type']:
            doc_types_str = ", ".join(template_fields['doc_type'])
            additional_info += f"\n\n**Documents Requested:**\n{doc_types_str}"

        if 'additional_context' in template_fields and template_fields['additional_context']:
            additional_context = convert_github_images_to_jira(template_fields['additional_context'])
            additional_info += f"\n\n**Additional Context:**\n{additional_context}"

        description_with_link = original_description + additional_info + github_link_text
        issue_data['fields']["description"] = description_with_link[:32767]

        # Affected locations from Gitea
        affected_locations = get_affected_locations_for_org(github_org)
        issue_data["fields"][template_field_map["affected_locations"]] = [
            {"value": location} for location in affected_locations
        ]

        issue_data["fields"]["priority"] = {"name": HARDCODED_VALUES["priority"]}
        issue_data["fields"][template_field_map["estimated_effort"]] = {"id": HARDCODED_VALUES["estimated_effort"]}
        issue_data["fields"][template_field_map["tier"]] = {"id": HARDCODED_VALUES["tier"]}
        issue_data["fields"][template_field_map["pays_into"]] = [{"id": HARDCODED_VALUES["pays_into"]}]

        issue_data["fields"]["labels"] = ["demand", "github-import", repo_name]

        jira_issue = jira_client.create_issue(issue_data)

        if jira_issue:
            jira_key = jira_issue["key"]

            # Sync comments from GitHub to Jira
            comment_count = sync_comments_to_jira(jira_key, github_org, repo_name, issue_number)
            if comment_count > 0:
                logger.info("Synced %d comments to %s", comment_count, jira_key)

            comment_body = f"Dear customer, this issue has been reported, ticket {jira_key}."
            github_client.add_comment_to_issue(github_org, repo_name, issue_number, comment_body)
            github_client.add_label_to_issue(github_org, repo_name, issue_number, [IMPORTED_LABEL])

            successful_imports += 1
        else:
            failed_imports += 1

        time.sleep(0.5)

    return successful_imports, failed_imports, skipped_imports


def main():
    logger.info("=" * 80)
    logger.info("GitHub to JIRA Issue Importer for DEMAND")
    logger.info("=" * 80)

    try:
        repositories, repo_component_mapping = get_repositories_from_db()

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
                    issues = github_client.get_issues(github_org, repo_name)

                    if not issues:
                        continue

                    successful, failed, skipped = import_to_jira(
                        issues, repo_name, repo_component_mapping, github_org
                    )

                    total_successful += successful
                    total_failed += failed
                    total_skipped += skipped

                except Exception as e:
                    logger.error("Error processing %s/%s: %s", github_org, repo_name, str(e))
                    continue

        logger.info("=" * 80)
        logger.info("SUMMARY: Imported=%d, Failed=%d, Skipped=%d",
                   total_successful, total_failed, total_skipped)
        logger.info("=" * 80)

    except psycopg2.Error as e:
        logger.error("Database error: %s", str(e))


if __name__ == "__main__":
    main()
