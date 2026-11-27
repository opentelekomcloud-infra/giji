"""PR Scanner — runs on cron: imports new PRs and reconciles Jira with the real state of known PRs."""
import logging
import time
import os
from datetime import datetime, timezone
from config.connections import session          # retries 429/5xx, then raises

import pull_requests_importer as giji
from pr_handlers import import_pr, reconcile, FINAL_STAGES, STAGE_DONE

MAX_NEW_PRS_PER_RUN = int(os.getenv("MAX_NEW_PRS_PER_RUN", "2"))
# PRs merged before Giji ever saw them are imported only if merged after this moment (ISO 8601, UTC),
# so turning Giji on does not pull the whole merge history into Jira.
_imported_this_run = 0

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _ts(value):
    """ISO 8601 timestamp as an aware UTC datetime; a value without offset (e.g. "2026-11-26") is taken as UTC."""
    ts = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


# Checked at import time, so a bad override fails the job at start with a clear message
_raw_catchup = os.getenv("PR_CATCHUP_SINCE", "2026-11-26T00:00:00Z")
try:
    CATCHUP_SINCE = _ts(_raw_catchup)
except ValueError as e:
    raise SystemExit(f"PR_CATCHUP_SINCE={_raw_catchup!r} is not an ISO 8601 date/time: {e}")


def get_recent_prs(org, repo):
    """Open + recently updated closed PRs."""
    prs = []
    for state in ('open', 'closed'):
        url = f"{giji.GITHUB_API_URL}/repos/{org}/{repo}/pulls"
        response = session.get(url, headers=giji.GITHUB_HEADERS,
                               params={"state": state, "per_page": 30, "sort": "updated", "direction": "desc"},
                               timeout=30)
        if response.status_code == 200:
            prs.extend(response.json())
        else:
            logger.error("GitHub API %s for %s/%s (state=%s): %s",
                         response.status_code, org, repo, state, response.text[:200])
    return prs


def get_pr(org, repo, number):
    r = session.get(f"{giji.GITHUB_API_URL}/repos/{org}/{repo}/pulls/{number}",
                    headers=giji.GITHUB_HEADERS, timeout=30)
    return r.json() if r.status_code == 200 else None


def db_row(cur, pr_url):
    """(stage, merged, demand key) of a PR in the DB, or None."""
    cur.execute('SELECT "Giji Stage", "Github PR Merged", "Jira Demand Key" FROM giji_prs '
                'WHERE "Github PR URL" = %s', (pr_url,))
    return cur.fetchone()


def unfinished_rows(cur, repo):
    cur.execute("""
        SELECT "Github PR URL", "Jira Demand Key", "Giji Stage" FROM giji_prs
        WHERE "Github Repo" = %s AND "Jira Demand Key" IS NOT NULL
          AND COALESCE("Giji Stage", '') NOT IN %s
        ORDER BY "created_at"
    """, (repo, FINAL_STAGES))
    return [{'url': u, 'jira_key': k, 'stage': s} for u, k, s in cur.fetchall()]


def should_import(pr):
    if pr.get('draft'):
        return False
    if pr['state'] == 'open':
        return True
    # merged between two scans, never seen open: catch it up, but only recent ones
    return bool(pr.get('merged_at')) and _ts(pr['merged_at']) >= CATCHUP_SINCE


def scan_repo(org, repo, conn, cur):
    global _imported_this_run
    logger.info("Scanning %s/%s", org, repo)

    # 1. new PRs
    prs = get_recent_prs(org, repo)
    logger.info("Got %d PRs from GitHub API", len(prs))
    for pr in prs:
        row = db_row(cur, pr['html_url'])
        previous_demand = None
        if row is None:
            if not should_import(pr):
                continue
        else:
            stage, was_merged, key = row
            # closed without merge (flow finished as cancelled) and now open again: needs a new Demand
            if not (pr['state'] == 'open' and not pr.get('draft') and stage == STAGE_DONE and not was_merged):
                continue
            previous_demand = key or 'unknown'
        if _imported_this_run >= MAX_NEW_PRS_PER_RUN:
            logger.info("Import limit (%d) reached, %s waits for the next run", MAX_NEW_PRS_PER_RUN, pr['html_url'])
            continue
        logger.info("%s PR detected: %s (%s)", "Reopened" if previous_demand else "New", pr['html_url'],
                    "merged" if pr.get('merged_at') else pr['state'])
        try:
            if import_pr(pr, org, repo, conn, cur, previous_demand=previous_demand):
                _imported_this_run += 1
        except Exception:                                # one broken PR must not stop the others
            conn.rollback()
            logger.exception("Import failed for %s, will retry on the next run", pr['html_url'])

    # 2. every PR whose flow is not finished: compare GitHub with Jira and catch up
    for row in unfinished_rows(cur, repo):
        number = row['url'].rstrip('/').split('/')[-1]
        pr = get_pr(org, repo, number)
        if not pr:
            logger.warning("Could not fetch %s from GitHub, will retry", row['url'])
            continue
        try:
            reconcile(pr, row, conn, cur, repo)
        except Exception:                                # one broken PR must not stop the others
            conn.rollback()
            logger.exception("Reconcile failed for %s (%s), will retry on the next run",
                             row['url'], row['jira_key'])
        time.sleep(0.5)


def main():
    logger.info("=" * 60)
    logger.info("PR Scanner started — org: %s, repos: %s",
                giji.env_vars.github_org, giji.env_vars.monitored_repos)
    logger.info("=" * 60)

    me = session.get(f"{giji.GITHUB_API_URL}/user", headers=giji.GITHUB_HEADERS, timeout=30)
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
