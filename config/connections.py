"""
This script contains data classes and API clients for code reusing
"""

import base64
import logging
import os
import re
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yaml


# Global session with connection pooling
session = requests.Session()

# Retry strategy for transient failures
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)


class EnvVariables:
    required_env_vars = [
        "DB_HOST", "DB_PORT", "DB_CSV", "DB_USER", "DB_PASSWORD",
        "GITHUB_TOKEN", "GITHUB_API_URL", "GITHUB_ORG",
        "JIRA_API_TOKEN", "JIRA_API_URL",
        "BASE_GITEA_URL",
    ]

    def __init__(self):
        self.db_host = os.getenv("DB_HOST")
        self.db_port = os.getenv("DB_PORT")
        self.db_csv = os.getenv("DB_CSV")
        self.db_user = os.getenv("DB_USER")
        self.db_password = os.getenv("DB_PASSWORD")

        # GitHub — single org, multiple repos
        self.github_org = os.getenv("GITHUB_ORG")
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.github_fallback_token = os.getenv("GITHUB_FALLBACK_TOKEN")
        self.github_api_url = os.getenv("GITHUB_API_URL", "https://api.github.com")
        repos_str = os.getenv("MONITORED_REPOS", "")
        self.monitored_repos = [r.strip() for r in repos_str.split(",") if r.strip()]

        # Jira — importer bot + two approver bots
        self.jira_api_token = os.getenv("JIRA_API_TOKEN")
        self.jira_api_url = os.getenv("JIRA_API_URL")
        self.jira_approver1_token = os.getenv("JIRA_AUTOECOSQUAD_TOKEN")
        self.jira_approver2_token = os.getenv("JIRA_AUTOECOSQUAD2_TOKEN")
        # Certificate auth — OPTIONAL (prod: set -> mTLS, preprod: unset -> none)
        self.jira_cert_path = os.getenv("JIRA_CERT_PATH")
        self.jira_key_path = os.getenv("JIRA_KEY_PATH")

        # Gitea for affected locations
        base_gitea = os.getenv("BASE_GITEA_URL")
        gitea_path = "/repos/infra/otc-metadata-rework/contents/otc_metadata/data/cloud_environments/"
        self.gitea_url_envs = f"{base_gitea}{gitea_path}"
        self.gitea_token = os.getenv("GITEA_TOKEN")

        # Confluence
        self.confluence_url = os.getenv("CONFLUENCE_URL")
        self.confluence_token = os.getenv("CONFLUENCE_TOKEN")

        self.check_env_variables()

    def check_env_variables(self):
        for var in self.required_env_vars:
            if os.getenv(var) is None:
                raise Exception("Missing environment variable: %s" % var)


class Database:
    def __init__(self, env):
        self.db_host = env.db_host
        self.db_port = env.db_port
        self.db_user = env.db_user
        self.db_password = env.db_password
        self.logger = logging.getLogger(__name__)
        self._pool = None

    def get_pool(self, db_name, minconn=1, maxconn=10):
        """Get or create connection pool for database"""
        if self._pool is None:
            try:
                self._pool = psycopg2.pool.SimpleConnectionPool(
                    minconn,
                    maxconn,
                    host=self.db_host,
                    port=self.db_port,
                    dbname=db_name,
                    user=self.db_user,
                    password=self.db_password
                )
                self.logger.info("Connection pool created for database: %s", db_name)
            except psycopg2.Error as e:
                self.logger.error("Failed to create connection pool: %s", str(e))
                raise
        return self._pool

    @contextmanager
    def get_connection(self, db_name):
        """Context manager for safe connection handling"""
        pool = self.get_pool(db_name)
        conn = None
        try:
            conn = pool.getconn()
            yield conn
        except psycopg2.Error as e:
            self.logger.error("Database error (credentials hidden): %s", str(e).split('DETAIL')[0])
            raise
        finally:
            if conn:
                pool.putconn(conn)

    def connect_to_db(self, db_name):
        """Get connection from pool"""
        self.logger.info("Getting connection from pool for: %s", db_name)
        pool = self.get_pool(db_name)
        return pool.getconn()

    def close_pool(self):
        """Close all connections in pool"""
        if self._pool:
            self._pool.closeall()
            self.logger.info("Connection pool closed")


class GitHubClient:
    def __init__(self, env, timeout=30):
        self.api_url = env.github_api_url
        self.token = env.github_token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.logger = logging.getLogger(__name__)
        self.rate_limit_remaining = None
        self.rate_limit_reset = None

    def _check_rate_limit(self):
        """Check GitHub rate limit before making requests"""
        if self.rate_limit_remaining is not None and self.rate_limit_remaining < 10:
            reset_time = self.rate_limit_reset or time.time()
            wait_time = max(0, reset_time - time.time())
            if wait_time > 0:
                self.logger.warning("Rate limit low (%d remaining). Waiting %d seconds...",
                                    self.rate_limit_remaining, int(wait_time))
                time.sleep(wait_time + 1)

    def _update_rate_limit(self, response):
        """Update rate limit info from response headers"""
        try:
            self.rate_limit_remaining = int(response.headers.get('X-RateLimit-Remaining', 5000))
            self.rate_limit_reset = int(response.headers.get('X-RateLimit-Reset', time.time() + 3600))
        except (ValueError, TypeError):
            pass

    def get_issues(self, org, repo_name, state="open"):
        """Fetch issues from GitHub repository."""
        self._check_rate_limit()

        response = session.get(
            f"{self.api_url}/repos/{org}/{repo_name}/issues",
            params={"state": state},
            headers=self.headers,
            timeout=self.timeout
        )

        self._update_rate_limit(response)

        if response.status_code != 200:
            raise requests.RequestException(
                f"GitHub API request failed for {repo_name}: {response.status_code} {response.text}"
            )

        issues = response.json()
        return issues

    def get_all_issues_paginated(self, org, repo_name, state="open", per_page=100):
        """Fetch all issues with pagination support."""
        all_issues = []
        page = 1

        while True:
            self._check_rate_limit()

            response = session.get(
                f"{self.api_url}/repos/{org}/{repo_name}/issues",
                params={"state": state, "per_page": per_page, "page": page},
                headers=self.headers,
                timeout=self.timeout
            )

            self._update_rate_limit(response)

            if response.status_code != 200:
                raise requests.RequestException(
                    f"GitHub API request failed for {repo_name}: {response.status_code} {response.text}"
                )

            issues = response.json()
            if not issues:
                break

            all_issues.extend(issues)
            page += 1

            if len(issues) < per_page:
                break

        return all_issues

    def get_issue_comments(self, org, repo_name, issue_number):
        """Fetch comments from GitHub issue."""
        self._check_rate_limit()

        url = f"{self.api_url}/repos/{org}/{repo_name}/issues/{issue_number}/comments"

        try:
            response = session.get(url, headers=self.headers, timeout=self.timeout)
            self._update_rate_limit(response)

            if response.status_code == 200:
                return response.json()
            return []
        except Exception:
            return []

    def add_label_to_issue(self, org, repo_name, issue_number, labels):
        """Add labels to GitHub issue."""
        self._check_rate_limit()

        response = session.post(
            f"{self.api_url}/repos/{org}/{repo_name}/issues/{issue_number}/labels",
            headers=self.headers,
            json={"labels": labels},
            timeout=self.timeout
        )

        self._update_rate_limit(response)

        if response.status_code != 200:
            self.logger.warning(
                "Failed to add labels to issue #%s in %s: %s",
                issue_number, repo_name, response.status_code
            )
            return False
        return True

    def add_comment_to_issue(self, org, repo_name, issue_number, comment_body):
        """Add comment to GitHub issue."""
        self._check_rate_limit()

        response = session.post(
            f"{self.api_url}/repos/{org}/{repo_name}/issues/{issue_number}/comments",
            headers=self.headers,
            json={"body": comment_body},
            timeout=self.timeout
        )

        self._update_rate_limit(response)

        if response.status_code != 201:
            self.logger.warning(
                "Failed to add comment to GitHub issue #%s in %s: %s",
                issue_number, repo_name, response.status_code
            )
            return False
        return True

    def create_label(self, org, repo_name, label_config):
        """Create a label in a GitHub repository."""
        self._check_rate_limit()

        url = f"{self.api_url}/repos/{org}/{repo_name}/labels"

        response = session.post(
            url,
            json=label_config,
            headers=self.headers,
            timeout=self.timeout
        )

        self._update_rate_limit(response)

        if response.status_code == 201:
            return True, "created"
        elif response.status_code == 422:
            error_data = response.json()
            if "already_exists" in error_data.get("message", "").lower():
                return True, "already_exists"
            else:
                return False, f"validation_error: {error_data}"
        elif response.status_code == 403:
            return False, "permission_denied"
        elif response.status_code == 404:
            return False, "not_found"
        else:
            return False, f"error_{response.status_code}"

    def check_repo_permissions(self, org, repo_name):
        """Check permissions on specific repository."""
        self._check_rate_limit()

        url = f"{self.api_url}/repos/{org}/{repo_name}"
        response = session.get(url, headers=self.headers, timeout=self.timeout)

        self._update_rate_limit(response)

        if response.status_code == 200:
            repo_data = response.json()
            permissions = repo_data.get('permissions', {})
            return permissions.get('push', False)
        return False


class JiraClient:
    def __init__(self, env, timeout=60, token=None):
        self.api_url = env.jira_api_url
        self.token = token or env.jira_api_token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.logger = logging.getLogger(__name__)

        # Certificate auth
        self.cert = None
        if env.jira_cert_path and env.jira_key_path:
            self.cert = (env.jira_cert_path, env.jira_key_path)
            self.logger.info("Using certificate authentication for Jira")

    def search_issues(self, jql, max_results=1, fields=None):
        if fields is None:
            fields = ["summary"]

        response = session.post(
            f"{self.api_url}/rest/api/2/search",
            headers=self.headers,
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": fields
            },
            timeout=self.timeout,
            cert=self.cert,
            verify=True
        )

        if response.status_code != 200:
            self.logger.warning("Failed to search Jira: %s", response.status_code)
            return None

        return response.json()

    def create_issue(self, issue_data):
        response = session.post(
            f"{self.api_url}/rest/api/2/issue",
            json=issue_data,
            headers=self.headers,
            timeout=self.timeout,
            cert=self.cert,
            verify=True
        )

        if response.status_code == 201:
            return response.json()
        else:
            self.logger.error("Jira error: %s", response.text)
            return None

    def add_comment(self, issue_key, comment_text):
        """Add comment to Jira issue."""
        url = f"{self.api_url}/rest/api/2/issue/{issue_key}/comment"
        payload = {"body": comment_text}

        try:
            response = session.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
                cert=self.cert,
                verify=True
            )
            return response.status_code == 201
        except Exception:
            return False

    def check_issue_exists(self, github_issue_number, project_key, repo_name):
        """Check if GitHub issue already exists in Jira."""
        jql = f'project = {project_key} AND summary ~ "#{github_issue_number}" AND summary ~ "{repo_name}"'
        results = self.search_issues(jql)

        if results:
            return results.get("total", 0) > 0
        return False

    def get_transitions(self, issue_key):
        """Get available transitions for a Jira issue."""
        url = f"{self.api_url}/rest/api/2/issue/{issue_key}/transitions"
        response = session.get(
            url,
            headers=self.headers,
            timeout=self.timeout,
            cert=self.cert,
            verify=True
        )
        if response.status_code == 200:
            return {t['name']: t['id'] for t in response.json()['transitions']}
        self.logger.warning("Failed to get transitions for %s: %s", issue_key, response.status_code)
        return {}

    def wait_for_status(self, issue_key, target_status, max_attempts=10, delay=30):
        """Wait until issue reaches target status."""
        for attempt in range(max_attempts):
            url = f"{self.api_url}/rest/api/2/issue/{issue_key}?fields=status"
            response = session.get(url, headers=self.headers, timeout=self.timeout, cert=self.cert, verify=True)
            if response.status_code == 200:
                current_status = response.json()['fields']['status']['name']
                self.logger.info("%s current status: %s (attempt %d/%d)", issue_key, current_status, attempt + 1,
                                 max_attempts)
                if current_status == target_status:
                    return True
            time.sleep(delay)
        self.logger.warning("Timeout waiting for %s to reach status '%s'", issue_key, target_status)
        return False

    def get_linked_issues(self, issue_key):
        """Get all linked issues. Returns list of {key, issuetype, summary}."""
        url = f"{self.api_url}/rest/api/2/issue/{issue_key}?fields=issuelinks"
        response = session.get(url, headers=self.headers, timeout=self.timeout,
                               cert=self.cert, verify=True)
        if response.status_code != 200:
            self.logger.warning("Failed to get issue links for %s: %s", issue_key, response.status_code)
            return []

        links = []
        for link in response.json()['fields'].get('issuelinks', []):
            linked = link.get('inwardIssue') or link.get('outwardIssue')
            if linked:
                links.append({
                    'key': linked['key'],
                    'issuetype': linked['fields']['issuetype']['name'],
                    'summary': linked['fields'].get('summary', ''),
                })
        return links

    def wait_for_linked_issues(self, issue_key, match_fn, max_attempts=8, delay=15):
        """Poll until linked issues matching match_fn appear. Returns list of keys."""
        for attempt in range(max_attempts):
            links = self.get_linked_issues(issue_key)
            matched = [link['key'] for link in links if match_fn(link)]
            if matched:
                self.logger.info("%s: found linked issues %s (attempt %d)", issue_key, matched, attempt + 1)
                return matched
            self.logger.info("%s: waiting for linked issues... (attempt %d/%d)", issue_key, attempt + 1, max_attempts)
            time.sleep(delay)
        self.logger.warning("%s: no matching linked issues after %d attempts", issue_key, max_attempts)
        return []

    def transition_issue(self, issue_key, transition_id, resolution_id=None):
        """Perform a transition on a Jira issue."""
        url = f"{self.api_url}/rest/api/2/issue/{issue_key}/transitions"

        body = {"transition": {"id": transition_id}}

        if resolution_id:
            body["fields"] = {
                "resolution": {"id": resolution_id}
            }

        response = session.post(
            url,
            headers=self.headers,
            json=body,
            timeout=self.timeout,
            cert=self.cert,
            verify=True
        )
        if response.status_code == 204:
            self.logger.info("Successfully transitioned %s with transition %s", issue_key, transition_id)
            return True
        self.logger.error("Failed to transition %s: %s %s", issue_key, response.status_code, response.text)
        return False

    def get_issue_summary(self, issue_key):
        """Get summary of a Jira issue."""
        url = f"{self.api_url}/rest/api/2/issue/{issue_key}?fields=summary"
        response = session.get(url, headers=self.headers, timeout=self.timeout, cert=self.cert, verify=True)
        if response.status_code == 200:
            return response.json()['fields']['summary']
        return ""

    def get_change_guide_url(self, issue_key):
        """Get Change Guide URL from a Change ticket."""
        url = f"{self.api_url}/rest/api/2/issue/{issue_key}?fields=customfield_12202"
        response = session.get(
            url,
            headers=self.headers,
            timeout=self.timeout,
            cert=self.cert,
            verify=True
        )
        if response.status_code == 200:
            return response.json()['fields'].get('customfield_12202')
        return None

    def update_issue_fields(self, issue_key, fields):
        """Update fields on an existing issue."""
        url = f"{self.api_url}/rest/api/2/issue/{issue_key}"
        response = session.put(
            url,
            headers=self.headers,
            json={"fields": fields},
            timeout=self.timeout,
            cert=self.cert,
            verify=True
        )
        if response.status_code == 204:
            self.logger.info("Updated fields on %s: %s", issue_key, list(fields.keys()))
            return True
        self.logger.error("Failed to update %s: %s %s",
                          issue_key, response.status_code, response.text[:300])
        return False


class GiteaClient:
    def __init__(self, env, timeout=10):
        self.base_url = env.gitea_url_envs
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)
        self.headers = {}
        if hasattr(env, 'gitea_token') and env.gitea_token:
            self.headers = {"Authorization": f"token {env.gitea_token}"}

    def get_file_content(self, file_path):
        """Get decoded content of a file from Gitea."""
        try:
            file_url = f"{self.base_url}/{file_path}"
            response = session.get(file_url, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            file_content_base64 = response.json()['content']
            file_content = base64.b64decode(file_content_base64).decode('utf-8')

            return file_content

        except Exception as e:
            self.logger.error("Error fetching file from Gitea (%s): %s", file_path, e)
            return None

    def list_directory(self, dir_path=""):
        """List files in a directory on Gitea."""
        try:
            if dir_path:
                url = f"{self.base_url}/{dir_path}"
            else:
                url = self.base_url

            response = session.get(url, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            self.logger.error("Error listing Gitea directory: %s", e)
            return None

    def get_affected_locations_for_org(self, org_name):
        """Get affected locations from Gitea metadata for organization."""
        try:
            files = self.list_directory()

            if not files:
                return None

            yaml_files = [item for item in files if item['type'] == 'file' and item['name'].endswith('.yaml')]

            for file_info in yaml_files:
                file_name = file_info['name']
                file_content = self.get_file_content(file_name)

                if not file_content:
                    continue

                data = yaml.safe_load(file_content)
                public_org = data.get('public_org')

                if public_org == org_name:
                    affected_locations = data.get('affected_locations', [])
                    if affected_locations:
                        return affected_locations

            return None

        except Exception as e:
            self.logger.error("Error fetching affected locations from Gitea: %s", e)
            return None


class ConfluenceClient:
    def __init__(self, env, timeout=30):
        self.base_url = env.confluence_url
        self.token = env.confluence_token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def extract_page_id(page_url):
        """Pull pageId out of a change guide URL."""
        if not page_url:
            return None
        match = re.search(r'/pages/(\d+)', page_url)
        if match:
            return match.group(1)
        match = re.search(r'pageId=(\d+)', page_url)
        return match.group(1) if match else None

    def get_workflow_status(self, page_id):
        """Current Comala workflow state of a page"""
        url = f"{self.base_url}/rest/cw/1/content/{page_id}/status"
        response = session.get(url, headers=self.headers, timeout=self.timeout)
        if response.status_code == 200:
            return response.json()
        self.logger.warning("Failed to get workflow status for page %s: %s",
                            page_id, response.status_code)
        return None

    def approve_page(self, page_id, approval_name="Final"):
        """Approve a Comala workflow page (Review -> Final)"""
        url = f"{self.base_url}/rest/cw/1/content/{page_id}/approvals/approve"
        payload = {
            "name": approval_name,
            "comment": "",
            "user": "",
            "password": "",
            "parameters": []
        }
        response = session.put(url, headers=self.headers, json=payload,
                               timeout=self.timeout)
        if response.status_code == 200:
            state = response.json().get('state', {}).get('name')
            self.logger.info("Approved page %s, state is now '%s'", page_id, state)
            return True
        self.logger.error("Failed to approve page %s: %s %s",
                          page_id, response.status_code, response.text[:300])
        return False

    def approve_change_guide(self, page_url):
        """Approve a change guide by its URL. Idempotent."""
        page_id = self.extract_page_id(page_url)
        if not page_id:
            self.logger.error("Could not extract pageId from URL: %s", page_url)
            return False

        status = self.get_workflow_status(page_id)
        if status and status.get('state', {}).get('final'):
            self.logger.info("Page %s is already final, skipping approval", page_id)
            return True

        return self.approve_page(page_id)


class Timer:
    def __init__(self):
        self.start_time = None
        self.end_time = None

    def start(self):
        self.start_time = time.time()

    def stop(self):
        self.end_time = time.time()
        self.report()

    def report(self):
        if self.start_time and self.end_time:
            execution_time = self.end_time - self.start_time
            minutes, seconds = divmod(execution_time, 60)
            logging.info(f"Script executed in {int(minutes)} minutes {int(seconds)} seconds!")
        else:
            logging.error("Timer was not properly started or stopped")
