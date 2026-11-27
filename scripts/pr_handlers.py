"""PR handlers: import a PR as a Demand, then reconcile Jira with the PR's real state on every run.

Every step checks Jira first and only does what is still missing, so a run that was interrupted,
a Jira that was slow, or a PR that was approved and merged between two scans is caught up on the
next run. A stage is advanced only when every Jira read in that step succeeded and Jira has really
reached that point; a failed read raises instead of looking like "nothing left to do".
"""
import logging
import pull_requests_importer as giji
from change_guide import prepare_guide
from config.connections import session          # retries 429/5xx, then raises

logger = logging.getLogger(__name__)

# Progress of a PR through the Change flow, stored in giji_prs."Giji Stage"
STAGE_CREATED = 'created'      # Demand exists
STAGE_OPENED = 'opened'        # NoTestNoDoc done, Changes exist, Risk Assessment filled
STAGE_APPROVED = 'approved'    # Change Guides approved, PREPARE closed, Changes approved (EXECUTE exists)
STAGE_DONE = 'done'            # merged: EXECUTE closed / closed without merge: cancelled
STAGE_SKIPPED = 'skipped'      # merged PR seen too late and without a filled template: not imported
STAGE_MANUAL = 'manual'        # merged without an approving review: Giji does not approve, a human decides
FINAL_STAGES = (STAGE_DONE, STAGE_SKIPPED, STAGE_MANUAL)

# Short waits: whatever is not ready yet is picked up again on the next run
SHORT_WAIT = {"max_attempts": 2, "delay": 15}
APPROVE_WAIT = {"max_attempts": 1, "delay": 0}


class JiraReadError(RuntimeError):
    """A Jira read failed. Raised instead of returning an empty result, so that a failed call is never
    mistaken for "nothing left to do"; scan_repo catches it and the PR is retried on the next run."""


def _jira_get(path):
    c = giji.jira_client
    r = session.get(f"{c.api_url}/rest/api/2/{path}", headers=c.headers, cert=c.cert, timeout=c.timeout)
    if r.status_code != 200:
        raise JiraReadError(f"Jira GET {path}: HTTP {r.status_code} as {c.actor}")
    return r.json()


def _links(key):
    out = []
    for link in _jira_get(f"issue/{key}?fields=issuelinks")['fields'].get('issuelinks', []):
        linked = link.get('inwardIssue') or link.get('outwardIssue')
        if linked:
            out.append({'key': linked['key'],
                        'issuetype': linked['fields']['issuetype']['name'],
                        'summary': linked['fields'].get('summary', '')})
    return out


def _linked(key, match_fn):
    """Keys of linked issues matching match_fn. Raises JiraReadError if Jira did not answer."""
    return [link['key'] for link in _links(key) if match_fn(link)]


def _transitions(key):
    """Transitions available to the main client. Raises JiraReadError if Jira did not answer."""
    return {t['name']: t['id'] for t in _jira_get(f"issue/{key}/transitions")['transitions']}


def _summary(key):
    """Summary of an issue. Raises JiraReadError if Jira did not answer."""
    return _jira_get(f"issue/{key}?fields=summary")['fields']['summary']


def _status(key):
    """(status name, status category key: 'new' / 'indeterminate' / 'done'). Raises JiraReadError."""
    status = _jira_get(f"issue/{key}?fields=status")['fields']['status']
    return status['name'], status.get('statusCategory', {}).get('key')


def _is_change(link):
    return link['issuetype'] == 'Change' and link['key'].startswith('OTCPR-')


def _is_task(prefix):
    return lambda link: not link['key'].startswith('OTCPR-') and link['summary'].startswith(prefix)


def get_pr_review_state(pr):
    """Review state of a PR: 'approved' only with at least one approval and no outstanding change request."""
    url = (f"{giji.GITHUB_API_URL}/repos/{pr['base']['repo']['full_name']}"
           f"/pulls/{pr['number']}/reviews")
    response = session.get(url, headers=giji.GITHUB_HEADERS, timeout=30)
    if response.status_code != 200:
        return None

    latest = {}
    for review in response.json():
        state = review['state']
        if state in ('APPROVED', 'CHANGES_REQUESTED'):
            latest[review['user']['login']] = state

    if any(s == 'CHANGES_REQUESTED' for s in latest.values()):
        return 'changes_requested'
    if any(s == 'APPROVED' for s in latest.values()):
        return 'approved'
    return 'pending'


# ---------------------------------------------------------------- import

def import_pr(pr, org, repo, conn, cur, previous_demand=None):
    """Create the Demand for a PR. Returns True if a Demand was created and recorded.

    previous_demand: the cancelled Demand of a PR that was closed without merge and is now reopened;
    the PR still carries the imported label from then, and its DB row is reset to the new Demand.
    The rest of the flow (NoTestNoDoc, Changes, approvals, closure) is done by reconcile()."""
    pr_url = pr['html_url']
    pr_number = pr['number']
    merged = bool(pr.get('merged_at'))

    if not previous_demand and giji.is_pr_already_imported(org, repo, pr_number):
        logger.info("PR %s already has the imported label but is not in the DB, skipping", pr_url)
        return False

    fields = giji.parse_pr_body(pr.get('body') or '')
    missing = giji.missing_sections(fields)
    if missing:
        logger.warning("PR %s: template incomplete, missing: %s", pr_url, missing)
        if merged:
            # Nobody can fix the body of a merged PR any more: tell the author once, don't retry every run
            if giji.save_pr_to_db(conn, cur, pr_url=pr_url, pr_state=pr.get('state'), pr_merged=True,
                                  github_repo=repo, demand_url=None):
                giji.set_stage(conn, cur, pr_url, STAGE_SKIPPED)
                if giji.add_label_to_pr(org, repo, pr_number, [giji.TEMPLATE_LABEL]):
                    giji.add_comment_to_pr(
                        org, repo, pr_number,
                        "Giji did not import this PR into Jira: it was merged with these PR template sections "
                        "empty: " + ", ".join(missing) + ". Please create the Jira Demand by hand.")
        else:
            giji.notify_incomplete_template(org, repo, pr_number, missing)
        return False

    issue = giji.jira_creator.create_issue(giji.build_issue_data(pr, fields, pr['title'], repo))
    if not issue:
        logger.error("Failed to create Demand for %s", pr_url)
        return False

    jira_key = issue['key']
    logger.info("Created Demand %s for %s%s", jira_key, pr_url,
                " (merged before Giji saw it)" if merged
                else f" (reopened, previous Demand {previous_demand} was cancelled)" if previous_demand else "")
    recorded = (_reset_row(conn, cur, pr_url, jira_key) if previous_demand
                else _save_state(conn, cur, pr, repo, jira_key, {}))
    if not recorded:
        logger.error("Demand %s for %s could not be recorded in the DB, cancelling it", jira_key, pr_url)
        stop_id = giji.jira_client.get_transitions(jira_key).get('Stop')
        reason = "Giji could not record this Demand; a new one is created on the next run."
        if not stop_id or not giji.jira_client.transition_issue(jira_key, stop_id, comment=reason):
            logger.error("Could not cancel %s, cancel it in Jira by hand", jira_key)
        return False
    giji.set_stage(conn, cur, pr_url, STAGE_CREATED)

    giji.remove_label_from_pr(org, repo, pr_number, giji.TEMPLATE_LABEL)
    giji.add_label_to_pr(org, repo, pr_number, [giji.IMPORTED_LABEL])
    giji.add_comment_to_pr(
        org, repo, pr_number,
        f"Imported to Jira: [{jira_key}]({giji.env_vars.jira_api_url}/browse/{jira_key})"
        + (f" (the PR was reopened; the previous Demand {previous_demand} stays cancelled)"
           if previous_demand else ""))

    release_notes = fields.get('release_notes')
    if release_notes:
        giji.jira_client.add_comment(jira_key, f"*Release Notes (from GitHub PR):*\n\n{release_notes}")
    return True


# ---------------------------------------------------------------- steps (idempotent)

def _ensure_opened(pr, jira_key, fields, conn, cur, repo):
    """NoTestNoDoc, Changes, Risk Assessment. Returns the Change keys when complete, else []."""
    changes = _linked(jira_key, _is_change)
    if not changes:
        status, _ = _status(jira_key)
        if status == 'Ready':
            if not giji.jira_client.transition_issue(jira_key, "291"):
                logger.error("NoTestNoDoc transition failed for %s", jira_key)
                return []
            logger.info("Triggered NoTestNoDoc for %s", jira_key)
        else:
            logger.info("%s is '%s', no Changes yet", jira_key, status)
        changes = giji.jira_client.wait_for_linked_issues(jira_key, _is_change, **SHORT_WAIT)
        if not changes:
            logger.warning("%s: no Changes yet, will check again on the next run", jira_key)
            return []

    # Risk Assessment first: it does not depend on the guide, and fill_risk_assessment only writes
    # the fields Jira lets us edit in the Change's current status
    for change_key in changes:
        giji.fill_risk_assessment(change_key, fields)

    urls, complete = {}, True
    for change_key in changes:
        prefix = 'preprod' if _summary(change_key).startswith("PreProd:") else 'prod'
        urls[f'change_{prefix}_url'] = giji.jira_client.link(change_key)
        guide_url = giji.jira_client.get_change_guide_url(change_key)
        if guide_url:
            urls[f'changeguide_{prefix}_url'] = guide_url
        else:
            logger.info("%s: Change Guide not created yet, will check again on the next run", change_key)
            complete = False
    _save_state(conn, cur, pr, repo, jira_key, urls)          # keep the URLs found so far
    return changes if complete else []


def _ensure_approved(pr, changes, fields):
    """Change Guide, PREPARE, two approvals. Complete when every Change has its EXECUTE task."""
    complete = True
    for change_key in changes:
        if _linked(change_key, _is_task('EXECUTE:')):
            continue                                    # already approved

        giji.fill_risk_assessment(change_key, fields)

        guide_url = giji.jira_client.get_change_guide_url(change_key)
        if guide_url:
            page_id = giji.confluence_client.extract_page_id(guide_url)
            if giji.confluence_client.update_page_body(page_id, lambda body: prepare_guide(body, pr)):
                giji.confluence_client.approve_change_guide(guide_url)
            else:
                logger.error("Guide %s was not prepared, leaving it for a human", guide_url)

        for task in _linked(change_key, _is_task('PREPARE:')):
            if 'Done' in _transitions(task):
                giji.close_squad_task(task)
                logger.info("Closed PREPARE %s", task)

        # one quick try per run; if Approve is not offered yet, the next run tries again
        approved = giji.approve_change(change_key, **APPROVE_WAIT)
        logger.info("Change %s: %d approval(s) given this run", change_key, approved)

        if not giji.jira_client.wait_for_linked_issues(change_key, _is_task('EXECUTE:'), **SHORT_WAIT):
            logger.warning("%s: no EXECUTE task yet, will check again on the next run", change_key)
            complete = False
    return complete


def _ensure_merged(changes):
    """Close EXECUTE. Complete only when every EXECUTE task is in a final status."""
    complete = True
    for change_key in changes:
        tasks = _linked(change_key, _is_task('EXECUTE:'))
        if not tasks:
            # an approved Change always has its EXECUTE task: none means Jira is not there yet
            logger.warning("%s: no EXECUTE task found, will check again on the next run", change_key)
            complete = False
        for task in tasks:
            if _status(task)[1] == 'done':
                continue                                # already closed
            if not giji.close_squad_task(task) or _status(task)[1] != 'done':
                logger.warning("EXECUTE %s is not closed yet, will check again on the next run", task)
                complete = False
            else:
                logger.info("Closed EXECUTE %s", task)
    return complete


def _ensure_cancelled(pr_url, jira_key, changes):
    """Cancel the Changes and the Demand of a PR closed without merge.

    Returns (complete, stuck): complete when every one of them is in a final status; stuck lists the
    issues that are not final and offer no Stop transition, so Giji can never cancel them itself."""
    reason = f"Pull request was closed without merge: {pr_url}. Cancelled automatically by Giji."
    complete, stuck = True, []
    for key in changes + [jira_key]:
        if _status(key)[1] == 'done':
            continue                                    # already stopped or closed
        stop_id = _transitions(key).get('Stop')
        if not stop_id:
            stuck.append(key)
            complete = False
            continue
        if giji.jira_client.transition_issue(key, stop_id, comment=reason):
            logger.info("Cancelled %s", key)
        if _status(key)[1] != 'done':
            logger.warning("%s is not in a final status yet, will check again on the next run", key)
            complete = False
    return complete, stuck


def _hand_over(conn, cur, pr_url, jira_key, text):
    """Leave a note for a human on the Demand and stop reconciling the PR.
    The stage goes final only if the note was posted; otherwise the next run tries again."""
    if not giji.jira_client.add_comment(jira_key, text):
        logger.error("Could not post the hand-over comment on %s for %s, will retry on the next run",
                     jira_key, pr_url)
        return False
    giji.set_stage(conn, cur, pr_url, STAGE_MANUAL)
    logger.warning("PR %s handed over to a human on %s", pr_url, jira_key)
    return True


# ---------------------------------------------------------------- reconcile

def reconcile(pr, row, conn, cur, repo):
    """Bring Jira up to the PR's real state in GitHub, step by step, as far as Jira allows now."""
    pr_url = pr['html_url']
    jira_key = row['jira_key']
    stage = row['stage'] or STAGE_CREATED           # a row whose stage was never written starts over, safely
    merged = bool(pr.get('merged_at'))
    closed_unmerged = pr['state'] == 'closed' and not merged
    fields = giji.parse_pr_body(pr.get('body') or '')

    if closed_unmerged:
        complete, stuck = _ensure_cancelled(pr_url, jira_key, _linked(jira_key, _is_change))
        if complete:
            _finish(conn, cur, pr_url, merged=False)
        elif stuck:
            # their status offers no Stop: retrying forever would change nothing
            _hand_over(conn, cur, pr_url, jira_key,
                       f"The pull request {pr_url} was closed without merge, but Giji cannot cancel "
                       f"{', '.join(stuck)}: no Stop transition in the current status. Please cancel by hand.")
        return

    if stage == STAGE_CREATED:
        changes = _ensure_opened(pr, jira_key, fields, conn, cur, repo)
        if not changes:
            return
        stage = giji.set_stage(conn, cur, pr_url, STAGE_OPENED)
    else:
        changes = _linked(jira_key, _is_change)
        if not changes:
            # past STAGE_CREATED the Demand always has Changes: none means Jira is not there yet
            logger.warning("%s: no Changes returned by Jira, will check again on the next run", jira_key)
            return

    if stage == STAGE_OPENED:
        review = get_pr_review_state(pr)
        if review is None:
            logger.warning("Could not read reviews of %s, will check again on the next run", pr_url)
            return
        if review != 'approved':
            if merged:
                # The change is already live, but nobody approved it in GitHub: Giji does not approve the
                # Change in Jira on its own. Hand it over to a human and stop reconciling it.
                _hand_over(conn, cur, pr_url, jira_key,
                           f"The pull request {pr_url} was merged without an approving review "
                           f"(review state: {review}). Giji did not approve the Changes; "
                           f"please complete them by hand.")
            return
        logger.info("PR %s is approved%s, running the approval steps for %s",
                    pr_url, " and merged" if merged else "", jira_key)
        if not _ensure_approved(pr, changes, fields):
            return
        stage = giji.set_stage(conn, cur, pr_url, STAGE_APPROVED)
        _set_review_state(conn, cur, pr_url, 'approved')

    if stage == STAGE_APPROVED and merged:
        logger.info("PR %s is merged, closing EXECUTE for %s", pr_url, jira_key)
        if _ensure_merged(changes):
            _finish(conn, cur, pr_url, merged=True)


# ---------------------------------------------------------------- DB

def _finish(conn, cur, pr_url, merged):
    cur.execute("""
        UPDATE giji_prs SET "Github PR State" = 'closed', "Github PR Merged" = %s,
               "Giji Stage" = %s, "updated_at" = NOW()
        WHERE "Github PR URL" = %s
    """, (merged, STAGE_DONE, pr_url))
    conn.commit()
    logger.info("PR %s done (%s)", pr_url, "merged" if merged else "cancelled")


def _set_review_state(conn, cur, pr_url, state):
    cur.execute('UPDATE giji_prs SET "Github Review State" = %s, "updated_at" = NOW() WHERE "Github PR URL" = %s',
                (state, pr_url))
    conn.commit()


def _reset_row(conn, cur, pr_url, jira_key):
    """Point the row of a reopened PR to its new Demand and start its flow over. Returns True if written."""
    try:
        cur.execute("""
            UPDATE giji_prs SET "Jira Demand Key" = %s, "Jira Demand URL" = %s,
                   "Github PR State" = 'open', "Github PR Merged" = FALSE, "Github Review State" = NULL,
                   "Jira Change URL PreProd" = NULL, "Changeguide URL PreProd" = NULL,
                   "Jira Change URL Prod" = NULL, "Changeguide URL Prod" = NULL,
                   "Giji Stage" = %s, "updated_at" = NOW()
            WHERE "Github PR URL" = %s
        """, (jira_key, giji.jira_client.link(jira_key), STAGE_CREATED, pr_url))
        conn.commit()
        return cur.rowcount == 1
    except Exception as e:
        conn.rollback()
        logger.error("Failed to reset the DB row of %s: %s", pr_url, e)
        return False


def _save_state(conn, cur, pr, repo, jira_key, urls):
    """Insert/update PR state in DB. Returns True if the row was written."""
    return giji.save_pr_to_db(
        conn, cur,
        pr_url=pr['html_url'],
        pr_state=pr.get('state'),
        pr_merged=bool(pr.get('merged_at')),
        github_repo=repo,
        jira_demand_key=jira_key,
        demand_url=f"{giji.env_vars.jira_api_url}/browse/{jira_key}",
        **urls
    )
