"""PR Scanner — runs on cron, detects state changes, calls handlers."""
import logging
import time
import os
import requests

import pull_requests_importer as giji
from pr_handlers import (
    get_pr_review_state, on_pr_opened, on_pr_approved, on_pr_merged, on_pr_closed
)

MAX_NEW_PRS_PER_RUN = int(os.getenv("MAX_NEW_PRS_PER_RUN", "2"))
_imported_this_run = 0

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_all_prs(org, repo):
    """Fetch open + recently closed PRs."""
    prs = []
    for state in ('open', 'closed'):
        url = f"{giji.GITHUB_API_URL}/repos/{org}/{repo}/pulls"
        response = requests.get(url, headers=giji.GITHUB_HEADERS,
                                params={"state": state, "per_page": 30, "sort": "updated", "direction": "desc"},
                                timeout=30)
        if response.status_code == 200:
            prs.extend(response.json())
        else:
            logger.error("GitHub API %s for %s/%s (state=%s): %s",
                         response.status_code, org, repo, state, response.text[:200])
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
    global _imported_this_run
    logger.info("Scanning %s/%s", org, repo)
    prs = get_all_prs(org, repo)
    logger.info("Got %d PRs from GitHub API", len(prs))
    for pr in prs:
        pr_url = pr['html_url']
        pr_state = pr['state']
        pr_merged = bool(pr.get('merged_at'))

        db = get_db_state(cur, pr_url)
        logger.debug("PR %s: state=%s draft=%s in_db=%s",
                     pr_url, pr_state, pr.get('draft'), db is not None)

        if db is None:
            if pr_state == 'open' and not pr.get('draft'):
                if _imported_this_run >= MAX_NEW_PRS_PER_RUN:
                    logger.info("Import limit (%d) reached, %s waits for the next run",
                                MAX_NEW_PRS_PER_RUN, pr_url)
                    continue
                logger.info("New PR detected: %s", pr_url)
                on_pr_opened(pr, org, repo, conn, cur)
                if get_db_state(cur, pr_url) is not None:   # a Demand was actually created
                    _imported_this_run += 1
            continue

        jira_key = db['jira_key']
        if not jira_key:
            logger.warning("PR %s is in DB but has no Jira Demand Key, skipping", pr_url)
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
                on_pr_approved(pr_url, jira_key, conn, cur, pr)

    seen = {pr['html_url'] for pr in prs}
    cur.execute(
        'SELECT "Github PR URL", "Jira Demand Key" FROM giji_prs WHERE "Github Repo" = %s AND "Github PR State" = %s',
        (repo, 'open'))
    for pr_url, jira_key in cur.fetchall():
        if pr_url in seen or not jira_key:
            continue
        number = pr_url.rstrip('/').split('/')[-1]
        r = requests.get(f"{giji.GITHUB_API_URL}/repos/{org}/{repo}/pulls/{number}", headers=giji.GITHUB_HEADERS,
                         timeout=30)
        if r.status_code != 200:
            continue
        pr = r.json()
        if pr.get('merged_at'):
            logger.info("PR merged (late catch): %s", pr_url)
            on_pr_merged(pr_url, jira_key, conn, cur)
        elif pr['state'] == 'closed':
            logger.info("PR closed without merge (late catch): %s", pr_url)
            on_pr_closed(pr_url, jira_key, conn, cur)

        time.sleep(0.5)


def main():
    logger.info("=" * 60)
    logger.info("PR Scanner started — org: %s, repos: %s",
                giji.env_vars.github_org, giji.env_vars.monitored_repos)
    logger.info("=" * 60)

    me = requests.get(f"{giji.GITHUB_API_URL}/user", headers=giji.GITHUB_HEADERS, timeout=30)
    logger.info("GitHub token acts as: %s (expires %s)",
                me.json().get("login") if me.status_code == 200 else me.status_code,
                me.headers.get("github-authentication-token-expiration"))

    for c in (giji.jira_client, giji.jira_creator, giji.jira_approver1, giji.jira_approver2):
        logger.info("Jira client '%s' acts as: %s", c.label, c.whoami())

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
