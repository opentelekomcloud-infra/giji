"""GitHub to JIRA Issue Importer for BUGS"""
import logging
import os
import re
import time

import psycopg2
import yaml

from config.connections import Database, EnvVariables, GitHubClient, JiraClient, GiteaClient

env_vars = EnvVariables()
database = Database(env_vars)
github_client = GitHubClient(env_vars)
jira_client = JiraClient(env_vars)
gitea_client = GiteaClient(env_vars)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Jira configuration
IMPORTED_LABEL = "imported-to-jira"
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "BM")
ISSUE_TYPE = "Bug"

# Squad targeting
TARGET_SQUADS = os.getenv("TARGET_SQUADS", "Database Squad,Compute Squad").split(",")

REPO_TO_MASTER_COMPONENT = {
    "dedicated-host": env_vars.deh,
    "auto-scaling": env_vars.asg,
    "elastic-cloud-server": env_vars.ecs,
    "image-management-service": env_vars.ims,
    "bare-metal-server": env_vars.bms,
    "relational-database-service": env_vars.rds,
    "gaussdb-opengauss": env_vars.opengauss,
    "geminidb": env_vars.geminidb,
    "gaussdb-mysql": env_vars.mysql,
    "data-replication-service": env_vars.drs,
    "data-admin-service": env_vars.das,
    "distributed-database-middleware": env_vars.ddm,
    "document-database-service": env_vars.dds
}

HARDCODED_VALUES = {
    "bug_type": "Documentation",
    "affected_areas": "Production",
    "priority": "Medium"
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


def get_affected_locations_from_gitea(org_name):
    """Fetch affected locations from Gitea metadata"""
    try:
        files = gitea_client.list_directory()

        if not files:
            return None

        yaml_files = [item for item in files if item['type'] == 'file' and item['name'].endswith('.yaml')]

        for file_info in yaml_files:
            file_content = gitea_client.get_file_content(file_info['name'])

            if not file_content:
                continue

            data = yaml.safe_load(file_content)

            if data.get('public_org') == org_name:
                affected_locations = data.get('affected_locations', [])
                if affected_locations:
                    return affected_locations
                else:
                    return None

        return None

    except Exception as e:
        logger.error("Error fetching from Gitea: %s", e)
        return None


def get_affected_locations_for_org(org):
    """Get affected locations with Gitea fallback"""
    locations = get_affected_locations_from_gitea(org)

    if locations:
        return locations

    locations = FALLBACK_AFFECTED_LOCATIONS.get(org)

    if not locations:
        return ["EU-DE-03 AZ3 (Germany/Biere)"]

    return locations


def get_repositories_from_db():
    """Get repositories from target squads"""
    repositories = []
    repo_component_mapping = {}

    # Use context manager for safe database connection handling
    with database.get_connection(env_vars.db_csv) as conn:
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

            for repository, squad, title in results:
                repositories.append(repository)

                if repository in REPO_TO_MASTER_COMPONENT:
                    repo_component_mapping[repository] = REPO_TO_MASTER_COMPONENT[repository]

        except Exception as e:
            logger.error("Error querying database: %s", e)
            raise

    return repositories, repo_component_mapping


def determine_test_category_from_url(url):
    """Determine test category from Document URL"""
    if not url:
        return "QA"

    url_lower = url.lower()

    if "/umn/" in url_lower or url_lower.strip() == "umn":
        return "UAT"
    elif "/api-ref/" in url_lower:
        return "QA"
    else:
        return "QA"


def is_bug_issue(issue):
    """Check if issue is a bug"""
    labels = [label["name"].lower() for label in issue.get("labels", [])]
    if "bug" in labels:
        return True

    title = issue.get("title", "").upper()
    if title.startswith("[BUG]"):
        return True

    return False


def is_issue_already_imported(issue):
    """Check if issue has imported label"""
    labels = [label["name"] for label in issue.get("labels", [])]
    return IMPORTED_LABEL in labels


def parse_github_issue_body(issue_body):
    """Parse GitHub issue template fields"""
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


def convert_github_images_to_jira(text):
    """Convert GitHub image tags to Jira format"""
    if not text:
        return text

    pattern = r'<img[^>]+src="([^"]+)"[^>]*>'
    text = re.sub(pattern, lambda m: f"!{m.group(1)}!", text)

    markdown_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    text = re.sub(markdown_pattern, lambda m: f"!{m.group(2)}!", text)

    return text


def sync_comments_to_jira(jira_issue_key, org, repo, issue_number):
    """Sync GitHub comments to Jira"""
    comments = github_client.get_issue_comments(org, repo, issue_number)

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


def get_master_component_for_repo(repo_name, repo_component_mapping):
    """Get master component key for repository"""
    component_key = repo_component_mapping.get(repo_name)
    if not component_key:
        raise ValueError(f"Master component mapping missing for repository: {repo_name}")
    return component_key


def import_to_jira(issues, repo_name, repo_component_mapping, github_org):
    """Import GitHub issues to Jira"""
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

    for issue in issues:
        issue_number = issue.get("number")

        if "pull_request" in issue:
            continue

        if not is_bug_issue(issue):
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
                "issuetype": {"name": ISSUE_TYPE},
                "summary": f"[{repo_name}] {issue.get('title', f'GitHub Issue #{issue_number}')}"
            }
        }

        master_component_key = get_master_component_for_repo(repo_name, repo_component_mapping)
        issue_data["fields"][template_field_map["master_component"]] = [{"key": master_component_key}]

        github_issue_url = issue.get('html_url')
        github_link_text = (f"\n\n*Imported from [GitHub Issue #{issue_number}]({github_issue_url}) "
                            f"in repository {repo_name}*")

        original_description = ""
        if 'description' in template_fields:
            original_description = template_fields['description']
        else:
            original_description = issue.get("body", "")

        original_description = convert_github_images_to_jira(original_description)

        additional_info = ""
        if 'url' in template_fields and template_fields['url']:
            additional_info += f"\n\n**Document URL:**\n{template_fields['url']}"

        if 'additional_context' in template_fields and template_fields['additional_context']:
            additional_context = convert_github_images_to_jira(template_fields['additional_context'])
            additional_info += f"\n\n**Additional Context:**\n{additional_context}"

        description_with_link = original_description + additional_info + github_link_text
        issue_data['fields']["description"] = description_with_link[:32767]

        document_url = template_fields.get('url', '')
        test_category = determine_test_category_from_url(document_url)

        issue_data["fields"][template_field_map["test_category"]] = {
            "id": TEST_CATEGORY_IDS[test_category]
        }

        affected_locations = get_affected_locations_for_org(github_org)
        issue_data["fields"][template_field_map["affected_locations"]] = [
            {"value": location} for location in affected_locations
        ]

        issue_data["fields"][template_field_map["bug_type"]] = [{"value": HARDCODED_VALUES["bug_type"]}]
        issue_data["fields"][template_field_map["affected_areas"]] = [{"value": HARDCODED_VALUES["affected_areas"]}]

        if 'users_impact' in template_fields:
            issue_data["fields"][template_field_map["users_impact"]] = template_fields['users_impact']

        issue_data["fields"]["priority"] = {"name": HARDCODED_VALUES["priority"]}
        issue_data["fields"]["labels"] = ["bug", "github-import", repo_name]

        jira_issue = jira_client.create_issue(issue_data)

        if jira_issue:
            jira_key = jira_issue["key"]

            comment_count = sync_comments_to_jira(jira_key, github_org, repo_name, issue_number)
            if comment_count > 0:
                logger.info("Synced %d comments to %s", comment_count, jira_key)

            comment_body = (f"Dear customer, this issue has been reported, ticket {jira_key})")

            github_client.add_comment_to_issue(github_org, repo_name, issue_number, comment_body)
            github_client.add_label_to_issue(github_org, repo_name, issue_number, [IMPORTED_LABEL])

            successful_imports += 1
        else:
            failed_imports += 1

        # Rate limiting delay to prevent GitHub API throttling
        time.sleep(0.5)

    return successful_imports, failed_imports, skipped_imports


def main():
    logger.info("=" * 80)
    logger.info("GitHub to JIRA Issue Importer for BUGS")
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
        logger.info("SUMMARY: Imported: %s, Failed: %s, Skipped: %s",
                    total_successful, total_failed, total_skipped)
        logger.info("=" * 80)

    except psycopg2.Error as e:
        logger.error("Database error: %s", str(e))


if __name__ == "__main__":
    main()
