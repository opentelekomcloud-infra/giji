"""PR event handlers — called by scanner or webhooks."""
import logging

import requests

import pull_requests_importer as giji

logger = logging.getLogger(__name__)


def get_pr_review_state(pr):
    """Aggregate review state for a PR from GitHub API."""
    url = (f"{giji.GITHUB_API_URL}/repos/{pr['base']['repo']['full_name']}"
           f"/pulls/{pr['number']}/reviews")
    response = requests.get(url, headers=giji.GITHUB_HEADERS, timeout=30)
    if response.status_code != 200:
        return None

    latest = {}
    for review in response.json():
        state = review['state']
        if state in ('APPROVED', 'CHANGES_REQUESTED'):
            latest[review['user']['login']] = state

    if any(s == 'APPROVED' for s in latest.values()):
        return 'approved'
    if any(s == 'CHANGES_REQUESTED' for s in latest.values()):
        return 'changes_requested'
    return 'pending'


def on_pr_opened(pr, org, repo, conn, cur):
    """PR opened — create Demand + Changes, fill fields."""
    pr_url = pr['html_url']
    pr_number = pr['number']

    if giji.is_pr_already_imported(org, repo, pr_number):
        logger.info("PR %s already imported, skipping", pr_url)
        return

    fields = giji.parse_pr_body(pr.get('body', ''))
    if not fields.get('summary') and not fields.get('description'):
        logger.warning("PR %s has no template fields, skipping", pr_url)
        return

    issue = giji.jira_client.create_issue(giji.build_issue_data(pr, fields, pr['title'], repo))
    if not issue:
        logger.error("Failed to create Demand for %s", pr_url)
        return

    jira_key = issue['key']
    logger.info("Created Demand %s for %s", jira_key, pr_url)

    if not giji.jira_client.wait_for_status(jira_key, "Ready", max_attempts=10, delay=30):
        logger.error("Timeout waiting for Ready on %s", jira_key)
        _save_state(conn, cur, pr, repo, jira_key, {})
        return

    giji.jira_client.transition_issue(jira_key, "291")
    logger.info("Triggered NoTestNoDoc for %s", jira_key)

    changes = giji.jira_client.wait_for_linked_issues(
        jira_key, lambda link: link['issuetype'] == 'Change' and link['key'].startswith('OTCPR-'))

    urls = {}
    for change_key in changes:
        summary = giji.jira_client.get_issue_summary(change_key)
        prefix = 'preprod' if summary.startswith("PreProd:") else 'prod'
        urls[f'change_{prefix}_url'] = f"{giji.env_vars.jira_api_url}/browse/{change_key}"

        if giji.jira_client.wait_for_status(change_key, "Preparation", max_attempts=10, delay=30):
            giji.fill_risk_assessment(change_key, fields)
            urls[f'changeguide_{prefix}_url'] = giji.wait_for_change_guide(change_key)

    giji.add_label_to_pr(org, repo, pr_number, [giji.IMPORTED_LABEL])
    giji.add_comment_to_pr(
        org, repo, pr_number,
        f"Imported to Jira: [{jira_key}]({giji.env_vars.jira_api_url}/browse/{jira_key})")

    release_notes = fields.get('release_notes')
    if release_notes:
        giji.jira_client.add_comment(jira_key, f"*Release Notes (from GitHub PR):*\n\n{release_notes}")

    _save_state(conn, cur, pr, repo, jira_key, urls)
    logger.info("on_pr_opened complete for %s -> %s", pr_url, jira_key)


def on_pr_approved(pr_url, jira_key, conn, cur):
    """PR approved — approve guide, close PREPARE, two approvals."""
    logger.info("on_pr_approved: %s -> %s", pr_url, jira_key)

    changes = giji.jira_client.wait_for_linked_issues(
        jira_key, lambda link: link['issuetype'] == 'Change' and link['key'].startswith('OTCPR-'))

    for change_key in changes:
        guide_url = giji.jira_client.get_change_guide_url(change_key)
        if guide_url:
            giji.confluence_client.approve_change_guide(guide_url)
            logger.info("Approved guide for %s", change_key)

        prepare = giji.jira_client.wait_for_linked_issues(
            change_key,
            lambda link: not link['key'].startswith('OTCPR-') and link['summary'].startswith('PREPARE:'))
        for t in prepare:
            giji.close_squad_task(t)
            logger.info("Closed PREPARE %s", t)

        approved = giji.approve_change(change_key)
        if approved:
            logger.info("Change %s approved (%d)", change_key, approved)

    cur.execute("""
        UPDATE giji_prs SET "Github Review State" = 'approved', "updated_at" = NOW()
        WHERE "Github PR URL" = %s
    """, (pr_url,))
    conn.commit()


def on_pr_merged(pr_url, jira_key, conn, cur):
    """PR merged — close EXECUTE; Jira automation closes the rest."""
    logger.info("on_pr_merged: %s -> %s", pr_url, jira_key)

    changes = giji.jira_client.wait_for_linked_issues(
        jira_key, lambda link: link['issuetype'] == 'Change' and link['key'].startswith('OTCPR-'))

    for change_key in changes:
        execute = giji.jira_client.wait_for_linked_issues(
            change_key,
            lambda link: not link['key'].startswith('OTCPR-') and link['summary'].startswith('EXECUTE:'))
        for t in execute:
            giji.close_squad_task(t)
            logger.info("Closed EXECUTE %s", t)

    cur.execute("""
        UPDATE giji_prs
        SET "Github PR State" = 'closed', "Github PR Merged" = TRUE, "updated_at" = NOW()
        WHERE "Github PR URL" = %s
    """, (pr_url,))
    conn.commit()
    logger.info("on_pr_merged complete for %s", pr_url)


def on_pr_closed(pr_url, jira_key, conn, cur):
    """PR closed without merge — cancel Changes and Demand."""
    logger.info("on_pr_closed (unmerged): %s -> %s", pr_url, jira_key)

    changes = giji.jira_client.wait_for_linked_issues(
        jira_key, lambda link: link['issuetype'] == 'Change' and link['key'].startswith('OTCPR-'))

    for change_key in changes:
        transitions = giji.jira_client.get_transitions(change_key)
        stop_id = transitions.get('Stop')
        if stop_id:
            giji.jira_client.transition_issue(change_key, stop_id)
            logger.info("Cancelled Change %s", change_key)

    transitions = giji.jira_client.get_transitions(jira_key)
    stop_id = transitions.get('Stop')
    if stop_id:
        giji.jira_client.transition_issue(jira_key, stop_id)
        logger.info("Cancelled Demand %s", jira_key)

    cur.execute("""
        UPDATE giji_prs
        SET "Github PR State" = 'closed', "Github PR Merged" = FALSE, "updated_at" = NOW()
        WHERE "Github PR URL" = %s
    """, (pr_url,))
    conn.commit()


def _save_state(conn, cur, pr, repo, jira_key, urls):
    """Insert/update PR state in DB after on_pr_opened."""
    giji.save_pr_to_db(
        conn, cur,
        pr_url=pr['html_url'],
        pr_state=pr.get('state'),
        pr_merged=bool(pr.get('merged_at')),
        github_repo=repo,
        jira_demand_key=jira_key,
        demand_url=f"{giji.env_vars.jira_api_url}/browse/{jira_key}",
        **urls
    )
    conn.commit()
