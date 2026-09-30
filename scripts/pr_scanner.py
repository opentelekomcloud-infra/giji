"""PR Scanner — runs on cron, detects state changes, calls handlers."""
import logging
import time

import requests

import pull_requests_importer as giji
from pr_handlers import (
    get_pr_review_state, on_pr_opened, on_pr_approved, on_pr_merged, on_pr_closed
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_all_prs(org, repo):
    """Fetch open + recently closed PRs."""
    prs = []
    for state in ('open', 'closed'):
        url = f"{giji.GITHUB_API_URL}/repos/{org}/{repo}/pulls"
        response = requests.get(url, headers=giji.GITHUB_HEADERS,
                                params={"state": state, "per_page": 30, "sort": "updated"},
                                timeout=30)
        if response.status_code == 200:
            prs.extend(response.json())
    return prs


def get_db_state(cur, pr_url):
    cur.execute("""
        SELECT "Github PR State", "Github PR Merged", "Github Review State", "Jira Demand Key"
        FROM giji_prs WHERE "Github PR URL" = %s
    """, (pr_url,))
    row = cur.fetchone()
    if not row:
        return None
    return {'state': row[0], 'merged': row[1], 'review_state': row[2], 'jira_key': row[3]}


def scan_repo(org, repo, conn, cur):
    logger.info("Scanning %s/%s", org, repo)
    for pr in get_all_prs(org, repo):
        pr_url = pr['html_url']
        pr_state = pr['state']
        pr_merged = bool(pr.get('merged_at'))

        db = get_db_state(cur, pr_url)

        if db is None:
            if pr_state == 'open' and not pr.get('draft'):
                logger.info("New PR detected: %s", pr_url)
                on_pr_opened(pr, org, repo, conn, cur)
            continue

        jira_key = db['jira_key']
        if not jira_key:
            continue

        if pr_merged and not db['merged']:
            logger.info("PR merged: %s", pr_url)
            on_pr_merged(pr_url, jira_key, conn, cur)
            continue

        if pr_state == 'closed' and not pr_merged and db['state'] == 'open':
            logger.info("PR closed without merge: %s", pr_url)
            on_pr_closed(pr_url, jira_key, conn, cur)
            continue

        if pr_state == 'open' and db['state'] == 'open':
            review_state = get_pr_review_state(pr)
            if review_state == 'approved' and db['review_state'] != 'approved':
                logger.info("PR approved: %s", pr_url)
                on_pr_approved(pr_url, jira_key, conn, cur)

        time.sleep(0.5)


def main():
    logger.info("=" * 60)
    logger.info("PR Scanner started — org: %s, repos: %s",
                giji.env_vars.github_org, giji.env_vars.monitored_repos)
    logger.info("=" * 60)

    conn = giji.database.connect_to_db(giji.env_vars.db_csv)
    cur = conn.cursor()
    giji.create_prs_table(conn, cur)

    org = giji.env_vars.github_org
    for repo in giji.env_vars.monitored_repos:
        scan_repo(org, repo, conn, cur)

    conn.close()
    logger.info("PR Scanner finished")


if __name__ == "__main__":
    main()
