"""GitHub PR to JIRA Demand Importer"""
import logging
import os
import re
import time

import requests

from config import EnvVariables, JiraClient, GiteaClient, Database, ConfluenceClient
from config.constants import REPO_TO_MASTER_COMPONENT, template_field_map

env_vars = EnvVariables()
database = Database(env_vars)

jira_client = JiraClient(env_vars)
gitea_client = GiteaClient(env_vars)
confluence_client = ConfluenceClient(env_vars)

jira_approver1 = JiraClient(env_vars, token=env_vars.jira_approver1_token)
jira_approver2 = JiraClient(env_vars, token=env_vars.jira_approver2_token)

GITHUB_ORG = env_vars.github_org
GITHUB_API_URL = env_vars.github_api_url

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

IMPORTED_LABEL = "imported-to-jira"
PROJECT_KEY = "OTCPR"
ISSUE_TYPE_ID = "11001"

HARDCODED_VALUES = {
    "estimated_effort": "15104",
    "pays_into": "15204",
    "priority": "Medium",
    "tier": "17301",
    "affected_areas": [
        {"id": "10226"},  # Prod
        {"id": "10227"}   # PreProd
    ]
}

TEMPLATE_FIELD_MAP = template_field_map
RISK_FIELD_MAP = template_field_map

RISK_ASSESSMENT = {
    "impact": "Customer is planned to experience no impact at all",
    "justification": "General rollout",
    "rollback": "Yes, always possible during and after the change",
    "technical_risk": "Low",
}

FALLBACK_AFFECTED_LOCATIONS = [
    "EU-DE-01 AZ1 (Germany/Biere)",
    "EU-DE-02 AZ2 (Germany/Magdeburg)",
    "EU-DE-03 AZ3 (Germany/Biere)"
]

GITHUB_HEADERS = {
    "Authorization": f"token {env_vars.github_token}",
    "Accept": "application/vnd.github.v3+json"
}


def get_master_component(repo_name):
    """Master component for a repo, from the Vault-backed mapping."""
    component = REPO_TO_MASTER_COMPONENT.get(repo_name)
    if not component:
        logger.error("No master component mapped for repo '%s' — check Vault", repo_name)
    return component


def create_prs_table(conn_csv, cur_csv):
    logging.info("Creating giji_prs table...")
    try:
        cur_csv.execute(
            '''CREATE TABLE IF NOT EXISTS giji_prs (
            id SERIAL PRIMARY KEY,
            "Github PR URL" TEXT UNIQUE NOT NULL,
            "Github Repo" TEXT,
            "Github PR State" VARCHAR(20),
            "Github PR Merged" BOOLEAN DEFAULT FALSE,
            "Github Review State" VARCHAR(30),
            "Jira Demand Key" TEXT,
            "Jira Demand URL" TEXT,
            "Jira Change URL PreProd" TEXT,
            "Changeguide URL PreProd" TEXT,
            "Jira Change URL Prod" TEXT,
            "Changeguide URL Prod" TEXT,
            "created_at" TIMESTAMP DEFAULT NOW(),
            "updated_at" TIMESTAMP DEFAULT NOW()
        );'''
        )
        conn_csv.commit()
    except Exception as e:
        logging.error("giji_prs table: an error occurred while trying to create a table: %s", e)


def get_open_prs(org, repo):
    """Fetch open PRs from a GitHub repo"""
    url = f"{GITHUB_API_URL}/repos/{org}/{repo}/pulls"
    response = requests.get(
        url,
        headers=GITHUB_HEADERS,
        params={"state": "open", "per_page": 100},
        timeout=30
    )
    if response.status_code == 200:
        prs = response.json()
        logger.info("Found %d open PRs in %s", len(prs), repo)
        return prs
    logger.error("Failed to fetch PRs for %s: %s", repo, response.status_code)
    return []


def is_pr_already_imported(org, repo, pr_number):
    """Check if PR already has imported label"""
    url = f"{GITHUB_API_URL}/repos/{org}/{repo}/issues/{pr_number}/labels"
    response = requests.get(url, headers=GITHUB_HEADERS, timeout=30)
    if response.status_code == 200:
        labels = [l["name"] for l in response.json()]
        return IMPORTED_LABEL in labels
    return False


def add_label_to_pr(org, repo, pr_number, labels):
    """Add labels to PR"""
    url = f"{GITHUB_API_URL}/repos/{org}/{repo}/issues/{pr_number}/labels"
    response = requests.post(url, headers=GITHUB_HEADERS, json={"labels": labels}, timeout=30)
    return response.status_code == 200


def add_comment_to_pr(org, repo, pr_number, comment_body):
    """Add comment to PR"""
    url = f"{GITHUB_API_URL}/repos/{org}/{repo}/issues/{pr_number}/comments"
    response = requests.post(url, headers=GITHUB_HEADERS, json={"body": comment_body}, timeout=30)
    return response.status_code == 201


def parse_pr_body(body):
    """Parse PR template fields."""
    if not body:
        return {}

    fields = {}
    patterns = {
        'summary': r'## Summary:\s*\n([\s\S]*?)(?=\n## |\Z)',
        'description': r'## Description:\s*\n([\s\S]*?)(?=\n## |\Z)',
        'release_notes': r'## Release Notes:\s*\n([\s\S]*?)(?=\n## |\Z)',
        'risk_implementation': r'## Risk of Implementation:\s*\n([\s\S]*?)(?=\n## |\Z)',
        'risk_omission': r'## Risk of Omission:\s*\n([\s\S]*?)(?=\n## |\Z)',
    }

    for field, pattern in patterns.items():
        match = re.search(pattern, body, re.DOTALL)
        if match:
            value = match.group(1).strip()
            value = re.sub(r'<!--.*?-->', '', value, flags=re.DOTALL).strip()
            if value:
                fields[field] = value

    return fields


def get_affected_locations(org):
    """Get affected locations from Gitea or fallback"""
    locations = gitea_client.get_affected_locations_for_org(org)
    if locations:
        return locations
    logger.info("Using fallback affected locations")
    return FALLBACK_AFFECTED_LOCATIONS


def get_affected_areas(pr):
    """Determine affected areas from the PR target branch."""
    base = pr.get('base', {}).get('ref', '')
    if base == 'main':
        return [{"id": "10226"}]        # Production
    if base == 'preprod':
        return [{"id": "10227"}]        # PreProd
    # logger.warning("Unknown target branch '%s', defaulting to both areas", base)
    # return HARDCODED_VALUES["affected_areas"]


def build_jira_description(pr, fields, repo):
    pr_url = pr.get('html_url')
    pr_number = pr.get('number')
    pr_title = pr.get('title')
    pr_author = pr.get('user', {}).get('login', 'unknown')

    parts = []
    if fields.get('description'):
        parts.append(fields['description'])

    parts.append(
        f"\n\n*Imported from [GitHub PR #{pr_number}: {pr_title}]({pr_url}) "
        f"by {pr_author} in {repo}*"
    )
    return "".join(parts)


def save_pr_to_db(conn_csv, cur_csv, pr_url, pr_state, pr_merged, demand_url,
                  jira_demand_key=None, github_repo=None,
                  change_preprod_url=None,
                  changeguide_preprod_url=None,
                  change_prod_url=None,
                  changeguide_prod_url=None):
    try:
        cur_csv.execute("""
            INSERT INTO giji_prs (
                "Github PR URL", "Github Repo", "Github PR State", "Github PR Merged",
                "Jira Demand Key", "Jira Demand URL",
                "Jira Change URL PreProd", "Changeguide URL PreProd",
                "Jira Change URL Prod", "Changeguide URL Prod"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT ("Github PR URL") DO UPDATE SET
                "Github PR State" = EXCLUDED."Github PR State",
                "Github PR Merged" = EXCLUDED."Github PR Merged",
                "Jira Demand Key" = COALESCE(EXCLUDED."Jira Demand Key", giji_prs."Jira Demand Key"),
                "Jira Demand URL" = COALESCE(EXCLUDED."Jira Demand URL", giji_prs."Jira Demand URL"),
                "Jira Change URL PreProd" = COALESCE(
                    EXCLUDED."Jira Change URL PreProd",
                    giji_prs."Jira Change URL PreProd"),
                "Changeguide URL PreProd" = COALESCE(
                    EXCLUDED."Changeguide URL PreProd",
                    giji_prs."Changeguide URL PreProd"),
                "Jira Change URL Prod" = COALESCE(EXCLUDED."Jira Change URL Prod", giji_prs."Jira Change URL Prod"),
                "Changeguide URL Prod" = COALESCE(EXCLUDED."Changeguide URL Prod", giji_prs."Changeguide URL Prod"),
                "updated_at" = NOW()
        """, (pr_url, github_repo, pr_state, pr_merged, jira_demand_key, demand_url,
              change_preprod_url, changeguide_preprod_url,
              change_prod_url, changeguide_prod_url))
        conn_csv.commit()
        logger.info("Saved PR %s to DB", pr_url)
    except Exception as e:
        logger.error("Failed to save PR to DB: %s", e)
        conn_csv.rollback()


def wait_for_change_guide(change_key, max_attempts=8, delay=15):
    """Poll until change guide URL appears on the Change"""
    for attempt in range(max_attempts):
        url = jira_client.get_change_guide_url(change_key)
        if url:
            logger.info("%s: change guide URL found (attempt %d)", change_key, attempt + 1)
            return url
        logger.info("%s: waiting for change guide URL... (attempt %d/%d)",
                    change_key, attempt + 1, max_attempts)
        time.sleep(delay)
    logger.warning("%s: no change guide URL after %d attempts", change_key, max_attempts)
    return None


def build_issue_data(pr, fields, pr_title, repo):
    summary = fields.get('summary') or pr_title
    description = build_jira_description(pr, fields, repo)
    return {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "issuetype": {"id": ISSUE_TYPE_ID},
            "summary": f"[{repo}] {summary}",
            "description": description[:32767],
            TEMPLATE_FIELD_MAP["master_component"]: [{"key": get_master_component(repo)}],
            TEMPLATE_FIELD_MAP["affected_locations"]: [
                {"value": loc} for loc in get_affected_locations(GITHUB_ORG)
            ],
            TEMPLATE_FIELD_MAP["affected_areas"]: get_affected_areas(pr),
            "priority": {"name": HARDCODED_VALUES["priority"]},
            TEMPLATE_FIELD_MAP["estimated_effort"]: {"id": HARDCODED_VALUES["estimated_effort"]},
            TEMPLATE_FIELD_MAP["tier"]: {"id": HARDCODED_VALUES["tier"]},
            TEMPLATE_FIELD_MAP["pays_into"]: [{"id": HARDCODED_VALUES["pays_into"]}],
            "labels": ["github-pr-import", repo],
        }
    }


def close_squad_task(task_key):
    """Close squad-related task (ES-XXX) as Done"""
    transitions = jira_client.get_transitions(task_key)
    done_id = transitions.get('Done')
    if not done_id:
        logger.warning("No Done transition available for %s", task_key)
        return False
    return jira_client.transition_issue(task_key, done_id, resolution_id="10000")


def fill_risk_assessment(change_key, fields):
    """Fill Risk Assessment tab on a Change ticket"""
    payload = {
        RISK_FIELD_MAP["impact"]: {"value": RISK_ASSESSMENT["impact"]},
        RISK_FIELD_MAP["justification"]: {"value": RISK_ASSESSMENT["justification"]},
        RISK_FIELD_MAP["rollback"]: {"value": RISK_ASSESSMENT["rollback"]},
        RISK_FIELD_MAP["technical_risk"]: {"value": RISK_ASSESSMENT["technical_risk"]},
    }
    if fields.get('risk_implementation'):
        payload[RISK_FIELD_MAP["risk_implementation"]] = fields['risk_implementation']
    if fields.get('risk_omission'):
        payload[RISK_FIELD_MAP["risk_omission"]] = fields['risk_omission']
    return jira_client.update_issue_fields(change_key, payload)


def approve_change(change_key, max_attempts=6, delay=10):
    """Wait for Approve transition to appear, then approve with each account."""
    approved = 0
    for client, label in ((jira_approver1, "autoecosquad"), (jira_approver2, "autoecosquad2")):
        approve_id = None
        for attempt in range(max_attempts):
            transitions = client.get_transitions(change_key)
            approve_id = transitions.get('Approve')
            if approve_id:
                break
            logger.info("%s: waiting for Approve transition as %s... available: %s (attempt %d/%d)",
                        change_key, label, list(transitions.keys()), attempt + 1, max_attempts)
            time.sleep(delay)

        if not approve_id:
            logger.warning("%s: Approve never appeared for %s", change_key, label)
            continue

        if client.transition_issue(change_key, approve_id):
            logger.info("%s approved by %s", change_key, label)
            approved += 1
        time.sleep(2)
    return approved


def process_change(change_key, fields):
    """Process one OTCPR-change. Returns dict with URLs"""
    result = {
        'is_preprod': False,
        'change_url': f"{env_vars.jira_api_url}/browse/{change_key}",
        'changeguide_url': None,
        'squad_url': None,
    }

    change_summary = jira_client.get_issue_summary(change_key)
    result['is_preprod'] = change_summary.startswith("PreProd:")
    logger.info("Change %s: '%s' (preprod=%s)", change_key, change_summary, result['is_preprod'])

    if not jira_client.wait_for_status(change_key, "Preparation", max_attempts=10, delay=30):
        logger.warning("Timed out waiting for %s to reach Preparation", change_key)
        return result

    result['changeguide_url'] = wait_for_change_guide(change_key)

    if fill_risk_assessment(change_key, fields):
        logger.info("Filled risk assessment on %s", change_key)

    if result['changeguide_url']:
        if confluence_client.approve_change_guide(result['changeguide_url']):
            logger.info("Approved change guide for %s", change_key)
        else:
            logger.error("Failed to approve change guide for %s", change_key)
    else:
        logger.warning("No change guide URL on %s", change_key)

    squad_tasks = jira_client.wait_for_linked_issues(
        change_key,
        lambda link: not link['key'].startswith('OTCPR-') and l['summary'].startswith('PREPARE:')
    )
    for task_key in squad_tasks:
        result['squad_url'] = f"{env_vars.jira_api_url}/browse/{task_key}"
        if close_squad_task(task_key):
            logger.info("Closed PREPARE task %s", task_key)

    approved = approve_change(change_key)
    if approved:
        logger.info("Change %s approved %d time(s)", change_key, approved)
        execute_tasks = jira_client.wait_for_linked_issues(
            change_key,
            lambda link: not link['key'].startswith('OTCPR-') and l['summary'].startswith('EXECUTE:')
        )
        for task_key in execute_tasks:
            if close_squad_task(task_key):
                logger.info("Closed EXECUTE task %s", task_key)
    else:
        logger.error("Failed to approve change %s", change_key)

    return result


def process_demand(jira_key, fields):
    """Run Demand through NoTestNoDoc and process all the Changes"""
    urls = {
        'change_preprod_url': None, 'squad_preprod_url': None, 'changeguide_preprod_url': None,
        'change_prod_url': None, 'squad_prod_url': None, 'changeguide_prod_url': None,
    }

    if not jira_client.wait_for_status(jira_key, "Ready", max_attempts=10, delay=20):
        logger.error("Timed out waiting for %s to reach Ready status", jira_key)
        return urls

    if not jira_client.transition_issue(jira_key, "291"):
        logger.error("Failed to trigger NoTestNoDoc transition for %s", jira_key)
        return urls

    logger.info("Triggered NoTestNoDoc transition for %s", jira_key)

    changes = jira_client.wait_for_linked_issues(
        jira_key,
        lambda link: link['issuetype'] == 'Change' and l['key'].startswith('OTCPR-')
    )

    for change_key in changes:
        r = process_change(change_key, fields)
        if not r:
            logger.error("process_change returned nothing for %s, skipping", change_key)
            continue
        prefix = 'preprod' if r['is_preprod'] else 'prod'
        urls[f'change_{prefix}_url'] = r['change_url']
        urls[f'squad_{prefix}_url'] = r['squad_url']
        urls[f'changeguide_{prefix}_url'] = r['changeguide_url']

    return urls


def import_pr_to_jira(pr, org, repo, conn_csv, cur_csv):
    """Import single PR to Jira"""
    pr_number = pr.get('number')
    pr_title = pr.get('title', f'PR #{pr_number}')

    if pr.get('draft'):
        logger.info("Skipping draft PR #%d", pr_number)
        return "skipped"

    if is_pr_already_imported(org, repo, pr_number):
        logger.info("PR #%d already imported, skipping", pr_number)
        return "skipped"

    if jira_client.check_issue_exists(pr_number, PROJECT_KEY, repo):
        add_label_to_pr(org, repo, pr_number, [IMPORTED_LABEL])
        return "skipped"

    fields = parse_pr_body(pr.get('body', ''))
    if not fields.get('summary') and not fields.get('description'):
        logger.warning("PR #%d has no template fields, skipping", pr_number)
        return "skipped"

    jira_issue = jira_client.create_issue(build_issue_data(pr, fields, pr_title, repo))
    if not jira_issue:
        logger.error("Failed to create Jira issue for PR #%d", pr_number)
        return "failed"

    jira_key = jira_issue["key"]
    logger.info("Created Jira issue %s for PR #%d", jira_key, pr_number)

    urls = process_demand(jira_key, fields)

    save_pr_to_db(
        conn_csv, cur_csv,
        pr_url=pr.get('html_url'),
        pr_state=pr.get('state'),
        pr_merged=bool(pr.get('merged_at')),
        github_repo=repo,
        jira_demand_key=jira_key,
        demand_url=f"{env_vars.jira_api_url}/browse/{jira_key}",
        **urls
    )

    release_notes = fields.get('release_notes')
    if release_notes:
        jira_client.add_comment(jira_key, f"*Release Notes (from GitHub PR):*\n\n{release_notes}")

    add_label_to_pr(org, repo, pr_number, [IMPORTED_LABEL])
    add_comment_to_pr(
        org, repo, pr_number,
        f"This PR has been imported to Jira: [{jira_key}]"
        f"({env_vars.jira_api_url}/browse/{jira_key})"
    )

    return "created"


def get_monitored_repos():
    """Repos to monitor, from Vault (MONITORED_REPOS=comma,separated)."""
    repos = os.getenv("MONITORED_REPOS", "").split(",")
    return [r.strip() for r in repos if r.strip()]


def main():
    repos = get_monitored_repos()
    logger.info("=" * 80)
    logger.info("GitHub PR to JIRA Demand Importer")
    logger.info("Org: %s | Repos: %s", GITHUB_ORG, repos)
    logger.info("=" * 80)

    conn_csv = database.connect_to_db(env_vars.db_csv)
    cur_csv = conn_csv.cursor()
    create_prs_table(conn_csv, cur_csv)

    created = skipped = failed = 0

    for repo in repos:
        prs = get_open_prs(GITHUB_ORG, repo)
        for pr in prs:
            result = import_pr_to_jira(pr, GITHUB_ORG, repo, conn_csv, cur_csv)
            if result == "created":
                created += 1
            elif result == "skipped":
                skipped += 1
            else:
                failed += 1
            time.sleep(0.5)

    logger.info("=" * 80)
    logger.info("SUMMARY: Created=%d, Skipped=%d, Failed=%d", created, skipped, failed)

    conn_csv.commit()
    conn_csv.close()


if __name__ == "__main__":
    main()
