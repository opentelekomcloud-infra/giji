"""Scenario tests for the PR scanner's reconcile loop against a fake Jira, GitHub and DB (no network).

Run: pytest tests/test_reconcile.py   (or: python tests/test_reconcile.py)
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))

FAIL_JIRA = None        # HTTP status every Jira read returns while set, e.g. 401
FAIL_STATE_READS = None  # HTTP status only status/transitions reads return while set (links still work)
SAVE_FAILS = False      # save_pr_to_db fails while set
COMMENT_FAILS = False   # Jira add_comment fails while set
FAIL_SUMMARY = None     # HTTP status only summary reads return while set
GH = {}                 # PR number -> GitHub PR dict
REVIEWS = {}            # PR number -> list of (reviewer, state)
LABELS = {}             # PR number -> labels added by Giji


class FakeJira:
    """Jira with just enough workflow: NoTestNoDoc -> Changes (+PREPARE), 2 approvals -> EXECUTE."""

    def __init__(self):
        self.issues = {}
        self.n = 45000
        self.api_url = "https://jira"
        self.headers = {}
        self.cert = None
        self.timeout = 1
        self.actor = "main/bot"
        self.changes_delay = 0      # runs Jira needs to create the Changes after NoTestNoDoc
        self.pending = []

    def new(self, typ, summary, status):
        self.n += 1
        key = f"{'OTCPR' if typ in ('Demand', 'Change') else 'ES'}-{self.n}"
        self.issues[key] = dict(status=status, links=[], type=typ, summary=summary, guide=None, approvals=0)
        return key

    def link(self, key):
        return f"{self.api_url}/browse/{key}"

    def create_issue(self, data):
        return {'key': self.new('Demand', data['fields']['summary'], 'Ready')}

    def get_linked_issues(self, key):
        return [dict(key=k, issuetype=self.issues[k]['type'], summary=self.issues[k]['summary'])
                for k in self.issues[key]['links']]

    def wait_for_linked_issues(self, key, match_fn, max_attempts=8, delay=15):
        return [link['key'] for link in self.get_linked_issues(key) if match_fn(link)]

    def tick(self):
        """Jira's automation, between two scanner runs."""
        still = []
        for demand, runs in self.pending:
            if runs > 0:
                still.append((demand, runs - 1))
                continue
            for env in ('Prod', 'PreProd'):
                change = self.new('Change', f"{env}: {self.issues[demand]['summary']}", 'Preparation')
                self.issues[change]['guide'] = 'https://confluence/pages/1'
                self.issues[change]['links'].append(self.new('Task', f"PREPARE: {change}", 'Open'))
                self.issues[demand]['links'].append(change)
        self.pending = still

    def transition_issue(self, key, tid, resolution_id=None, comment=None):
        issue = self.issues[key]
        if tid == "291" and issue['status'] == 'Ready':
            issue['status'] = 'Accepted'
            self.pending.append((key, self.changes_delay))
            return True
        if tid == 'done' and issue['status'] == 'Open':
            issue['status'] = 'Done'
            return True
        if tid == 'stop' and issue['status'] not in ('Cancelled', 'Done'):
            issue['status'] = 'Cancelled'
            return True
        if tid == 'approve' and issue['approvals'] < 2:
            issue['approvals'] += 1
            if issue['approvals'] == 2:
                issue['links'].append(self.new('Task', f"EXECUTE: {key}", 'Open'))
            return True
        return False

    def get_transitions(self, key):
        issue = self.issues[key]
        transitions = {}
        if issue['type'] == 'Task' and issue['status'] == 'Open':
            transitions['Done'] = 'done'
        if issue['type'] in ('Change', 'Demand') and issue['status'] not in ('Cancelled', 'Done') \
                and not issue.get('no_stop'):
            transitions['Stop'] = 'stop'
        if issue['type'] == 'Change' and issue['approvals'] < 2:
            transitions['Approve'] = 'approve'
        return transitions

    def get_issue_summary(self, key):
        # like the real client: a failed read comes back as an empty string
        return "" if (FAIL_JIRA or FAIL_SUMMARY) else self.issues[key]['summary']

    def get_change_guide_url(self, key):
        return self.issues[key]['guide']

    def add_comment(self, key, text):
        if COMMENT_FAILS:
            return False
        self.issues[key].setdefault('comments', []).append(text)
        return True


class FakeDB:
    """The few queries the scanner and handlers run against giji_prs."""

    def __init__(self):
        self.rows = {}
        self._result = []
        self.rowcount = 0

    def cursor(self):
        return self

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def execute(self, sql, params=()):
        self._result = []
        if sql.startswith('SELECT "Giji Stage", "Github PR Merged", "Jira Demand Key"'):
            r = self.rows.get(params[0])
            self._result = [(r['stage'], r.get('merged', False), r['key'])] if r else []
        elif sql.strip().startswith('UPDATE giji_prs SET "Jira Demand Key" = %s'):        # _reset_row
            key, _, stage, url = params
            self.rowcount = 0
            if url in self.rows:
                self.rows[url].update(key=key, stage=stage, merged=False)
                self.rowcount = 1
        elif 'SELECT "Github PR URL", "Jira Demand Key", "Giji Stage"' in sql:
            repo, final = params
            self._result = [(url, r['key'], r['stage']) for url, r in self.rows.items()
                            if r['repo'] == repo and r['key'] and (r['stage'] or '') not in final]
        elif sql.startswith('UPDATE giji_prs SET "Giji Stage" = %s'):
            self.rows[params[1]]['stage'] = params[0]
        elif '"Giji Stage" = %s, "updated_at"' in sql:          # _finish
            self.rows[params[2]].update(stage=params[1], merged=params[0])

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


class Resp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.headers = {}
        self.text = ''

    def json(self):
        return self._data


J = FakeJira()
D = FakeDB()


def fake_get(url, headers=None, params=None, timeout=None, cert=None):
    if url.startswith('https://jira/rest/api/2/issue/'):
        if FAIL_JIRA:
            return Resp(FAIL_JIRA, None)
        rest = url.split('/issue/')[1]
        key = rest.split('?')[0].split('/')[0]
        if FAIL_SUMMARY and 'fields=summary' in rest:
            return Resp(FAIL_SUMMARY, None)
        if FAIL_STATE_READS and (rest.endswith('/transitions') or 'fields=status' in rest):
            return Resp(FAIL_STATE_READS, None)
        if rest.endswith('/transitions'):
            return Resp(200, {'transitions': [{'name': n, 'id': i} for n, i in J.get_transitions(key).items()]})
        if 'fields=summary' in rest:
            return Resp(200, {'fields': {'summary': J.issues[key]['summary']}})
        if 'issuelinks' in rest:
            links = []
            for k in J.issues[key]['links']:
                fields = {'issuetype': {'name': J.issues[k]['type']}, 'summary': J.issues[k]['summary']}
                links.append({'outwardIssue': {'key': k, 'fields': fields}})
            return Resp(200, {'fields': {'issuelinks': links}})
        status = J.issues[key]['status']
        category = 'done' if status in ('Done', 'Cancelled') else 'indeterminate'
        return Resp(200, {'fields': {'status': {'name': status, 'statusCategory': {'key': category}}}})
    if url.endswith('/reviews'):
        number = int(url.split('/pulls/')[1].split('/')[0])
        return Resp(200, [{'state': s, 'user': {'login': who}} for who, s in REVIEWS.get(number, [])])
    if '/pulls/' in url:
        return Resp(200, GH[int(url.rstrip('/').split('/')[-1])])
    if url.endswith('/pulls'):
        return Resp(200, [p for p in GH.values() if p['state'] == params['state']])
    return Resp(200, {'login': 'otcbot'})


def _save(conn, cur, pr_url, pr_state, pr_merged, demand_url, jira_demand_key=None, github_repo=None, **kw):
    if SAVE_FAILS:
        return False
    D.rows.setdefault(pr_url, dict(repo=github_repo, key=None, stage=None))
    if jira_demand_key:
        D.rows[pr_url]['key'] = jira_demand_key
    return True


def _set_stage(conn, cur, url, stage):
    D.rows[url]['stage'] = stage
    return stage


def _add_label(org, repo, number, labels):
    LABELS.setdefault(number, []).extend(labels)
    return True


def _approve_change(key, **kwargs):
    if J.issues[key]['approvals'] >= 2:
        return 0
    return sum(J.transition_issue(key, 'approve') for _ in range(2))


def _fill_risk(key, fields):
    J.issues[key]['risk'] = True
    return True


def _parse(body):
    if not body:
        return {}
    return {'summary': 's', 'description': 'd', 'risk_implementation': 'ri', 'risk_omission': 'ro'}


# ---- fake modules the code under test imports
config = types.ModuleType('config')
connections = types.ModuleType('config.connections')
connections.session = types.SimpleNamespace(get=fake_get)
config.connections = connections
sys.modules['config'] = config
sys.modules['config.connections'] = connections
sys.modules['change_guide'] = types.SimpleNamespace(prepare_guide=lambda body, pr: body)

g = types.ModuleType('pull_requests_importer')
g.GITHUB_API_URL = 'https://gh'
g.GITHUB_HEADERS = {}
g.TEMPLATE_LABEL = 'giji-template-missing'
g.IMPORTED_LABEL = 'imported-to-jira'
g.jira_client = g.jira_creator = J
g.env_vars = types.SimpleNamespace(jira_api_url='https://jira', github_org='o', monitored_repos=['r'], db_csv='x')
g.is_pr_already_imported = lambda *a: False
g.parse_pr_body = _parse
g.missing_sections = lambda fields: [] if fields else ['Summary']
g.notify_incomplete_template = lambda *a: None
g.build_issue_data = lambda pr, fields, title, repo: {'fields': {'summary': f"[{repo}] {title}"}}
g.remove_label_from_pr = lambda *a, **k: True
g.add_comment_to_pr = lambda *a, **k: True
g.add_label_to_pr = _add_label
g.save_pr_to_db = _save
g.set_stage = _set_stage
g.fill_risk_assessment = _fill_risk
g.confluence_client = types.SimpleNamespace(extract_page_id=lambda url: 1, update_page_body=lambda p, fn: True,
                                            approve_change_guide=lambda url: True)
g.close_squad_task = lambda key: J.transition_issue(key, 'done')
g.approve_change = _approve_change
g.create_prs_table = lambda conn, cur: None
g.database = types.SimpleNamespace(connect_to_db=lambda name: D)
sys.modules['pull_requests_importer'] = g

import pr_handlers  # noqa: E402
import pr_scanner  # noqa: E402

pr_handlers.SHORT_WAIT = {"max_attempts": 1, "delay": 0}
pr_scanner.time.sleep = lambda s: None


def pr(number, state='open', merged_at=None, body='filled'):
    GH[number] = {'number': number, 'html_url': f'https://gh/o/r/pull/{number}', 'state': state,
                  'merged_at': merged_at, 'body': body, 'title': f'PR {number}', 'draft': False,
                  'base': {'repo': {'full_name': 'o/r'}, 'ref': 'main'}}


def merge(number, when):
    GH[number].update(state='closed', merged_at=when)


def run():
    pr_scanner._imported_this_run = 0
    pr_scanner.scan_repo('o', 'r', D, D)
    J.tick()


def stage(number):
    return D.rows.get(f'https://gh/o/r/pull/{number}', {}).get('stage')


def demand(number):
    return D.rows[f'https://gh/o/r/pull/{number}']['key']


def tasks(number, prefix):
    out = []
    for change in J.issues[demand(number)]['links']:
        out += [(t, J.issues[t]['status']) for t in J.issues[change]['links']
                if J.issues[t]['summary'].startswith(prefix)]
    return out


def all_done(number, prefix):
    found = tasks(number, prefix)
    return bool(found) and all(status == 'Done' for _, status in found)


def test_reconcile_scenarios():
    global FAIL_JIRA, FAIL_STATE_READS, SAVE_FAILS, COMMENT_FAILS, FAIL_SUMMARY

    # 1. slow Jira: the Changes appear two runs after NoTestNoDoc
    J.changes_delay = 2
    pr(1)
    run()
    assert stage(1) == 'created'
    run()
    assert stage(1) == 'created'
    run()
    run()
    assert stage(1) == 'opened'
    J.changes_delay = 0

    # 2. approved AND merged between two runs
    REVIEWS[1] = [('a', 'APPROVED')]
    merge(1, '2026-11-26T10:00:00Z')
    run()
    run()
    assert stage(1) == 'done'
    assert all_done(1, 'PREPARE') and all_done(1, 'EXECUTE')

    # 3. opened, approved and merged between two runs: never seen open
    pr(2, state='closed', merged_at='2026-11-26T11:00:00Z')
    REVIEWS[2] = [('a', 'APPROVED')]
    for _ in range(4):
        run()
    assert stage(2) == 'done' and all_done(2, 'EXECUTE')

    # 4. merged before PR_CATCHUP_SINCE: not imported
    pr(3, state='closed', merged_at='2026-10-01T00:00:00Z')
    run()
    assert stage(3) is None

    # 5. merged without a filled template: skipped, labelled, not retried
    pr(4, state='closed', merged_at='2026-11-26T12:00:00Z', body='')
    run()
    run()
    assert stage(4) == 'skipped' and 'giji-template-missing' in LABELS[4]

    # 6. closed without merge: Demand and Changes cancelled
    pr(5)
    run()
    run()
    GH[5].update(state='closed')
    run()
    assert stage(5) == 'done'
    assert J.issues[demand(5)]['status'] == 'Cancelled'
    assert all(J.issues[c]['status'] == 'Cancelled' for c in J.issues[demand(5)]['links'])

    # 7. open and not approved: waits at "opened", nothing approved
    pr(6)
    run()
    run()
    run()
    assert stage(6) == 'opened' and not tasks(6, 'EXECUTE')

    # 8. every Jira read fails (401) while an approved, merged PR is caught up: no stage moves
    pr(7)
    run()
    run()
    REVIEWS[7] = [('a', 'APPROVED')]
    merge(7, '2026-11-26T13:00:00Z')
    FAIL_JIRA = 401
    for _ in range(3):
        run()
    assert stage(7) == 'opened' and not tasks(7, 'EXECUTE')
    FAIL_JIRA = None
    run()
    run()
    assert stage(7) == 'done' and all_done(7, 'EXECUTE')

    # 8b. approved PR gets merged while only status/transitions reads fail: EXECUTE must not be
    #     counted as closed, and a closed-unmerged PR must not be counted as cancelled
    pr(14)
    pr(15)
    run()
    run()
    REVIEWS[14] = [('a', 'APPROVED')]
    run()
    assert stage(14) == 'approved'
    merge(14, '2026-11-26T13:30:00Z')
    GH[15].update(state='closed')
    FAIL_STATE_READS = 401
    for _ in range(3):
        run()
    assert stage(14) == 'approved' and not all_done(14, 'EXECUTE')
    assert stage(15) == 'opened'
    FAIL_STATE_READS = None
    run()
    assert stage(14) == 'done' and all_done(14, 'EXECUTE')
    assert stage(15) == 'done' and J.issues[demand(15)]['status'] == 'Cancelled'

    # 8c. Jira answers, but the Demand shows no Changes any more (e.g. links removed by hand):
    #     a merged PR must not be finished with nothing done
    pr(16)
    run()
    run()
    REVIEWS[16] = [('a', 'APPROVED')]
    run()
    assert stage(16) == 'approved'
    saved_links = J.issues[demand(16)]['links']
    J.issues[demand(16)]['links'] = []
    merge(16, '2026-11-26T13:40:00Z')
    run()
    assert stage(16) == 'approved'
    J.issues[demand(16)]['links'] = saved_links
    run()
    assert stage(16) == 'done'

    # 9. merged without an approving review: Giji gives no approvals, hands over to a human,
    #    but only once the note on the Demand was actually posted
    pr(8)
    run()
    run()
    merge(8, '2026-11-26T14:00:00Z')
    COMMENT_FAILS = True
    run()
    assert stage(8) == 'opened'
    COMMENT_FAILS = False
    run()
    assert stage(8) == 'manual' and not tasks(8, 'EXECUTE')
    assert any('without an approving review' in c for c in J.issues[demand(8)]['comments'])

    # 10. one approval plus an outstanding change request is not "approved"
    pr(9)
    run()
    run()
    REVIEWS[9] = [('a', 'APPROVED'), ('b', 'CHANGES_REQUESTED')]
    run()
    assert stage(9) == 'opened' and not tasks(9, 'EXECUTE')

    # 11. a row whose stage was never written is reconciled from the start
    pr(10)
    run()
    D.rows['https://gh/o/r/pull/10']['stage'] = None
    run()
    run()
    assert stage(10) == 'opened'

    # 12. DB insert fails after the Demand was created: the PR is not labelled as imported
    SAVE_FAILS = True
    pr(11)
    run()
    SAVE_FAILS = False
    assert 'imported-to-jira' not in LABELS.get(11, [])

    # 13. an exception while importing one PR does not stop the scan
    pr(12)
    pr(13)
    real = g.build_issue_data

    def boom(pr_, fields, title, repo):
        if pr_['number'] == 12:
            raise RuntimeError("boom")
        return real(pr_, fields, title, repo)

    g.build_issue_data = boom
    pr_scanner.MAX_NEW_PRS_PER_RUN = 10
    run()
    g.build_issue_data = real
    assert stage(12) is None and stage(13) == 'created'

    # 15. closed without merge, then reopened: a new Demand, the old one stays cancelled
    pr(17)
    run()
    run()
    old = demand(17)
    GH[17].update(state='closed')
    run()
    assert stage(17) == 'done' and J.issues[old]['status'] == 'Cancelled'
    GH[17].update(state='open')
    run()
    new = demand(17)
    assert new != old and stage(17) == 'created' and J.issues[old]['status'] == 'Cancelled'
    run()
    assert stage(17) == 'opened' and J.issues[new]['links']
    REVIEWS[17] = [('a', 'APPROVED')]
    merge(17, '2026-11-26T15:00:00Z')
    run()
    run()
    assert stage(17) == 'done' and all_done(17, 'EXECUTE')

    # 16. a merged PR whose flow is done is never re-imported, even though it is "in the DB, done"
    before = len(J.issues)
    run()
    assert demand(1) and stage(1) == 'done' and len(J.issues) == before

    # 17. closed without merge, but a Change offers no Stop in its status: handed over, not retried forever
    pr(18)
    run()
    run()
    stuck = J.issues[demand(18)]['links'][0]
    J.issues[stuck]['no_stop'] = True
    GH[18].update(state='closed')
    run()
    assert stage(18) == 'manual'
    assert any(stuck in c for c in J.issues[demand(18)]['comments'])

    # 18. the Changes exist but their summary cannot be read: no stage moves (no PreProd URL filed as Prod)
    pr(19)
    run()                                   # Demand + NoTestNoDoc; Jira creates the Changes after this run
    FAIL_SUMMARY = 404
    run()
    run()
    assert stage(19) == 'created'
    FAIL_SUMMARY = None
    run()
    assert stage(19) == 'opened'

    # 14. PR_CATCHUP_SINCE without a timezone is taken as UTC
    assert pr_scanner._ts("2026-11-26") <= pr_scanner._ts("2026-11-26T10:00:00Z")


if __name__ == '__main__':
    test_reconcile_scenarios()
    print("all scenarios passed")
