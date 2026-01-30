"""
This script contains data classes and API clients for code reusing
"""

import base64
import logging
import os
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
        "GITHUB_TOKEN", "GITHUB_API_URL", "GITHUB_ORGS",
        "JIRA_API_URL", "JIRA_CERT_PATH", "JIRA_KEY_PATH",
        "BASE_GITEA_URL", "DEH", "ASG", "ECS", "IMS", "BMS", "RDS", "OPENGAUSS",
        "GEMINIDB", "MYSQL", "DRS", "DAS", "DDM", "DDS"
    ]

    def __init__(self):
        self.db_host = os.getenv("DB_HOST")
        self.db_port = os.getenv("DB_PORT")
        self.db_csv = os.getenv("DB_CSV")
        self.db_user = os.getenv("DB_USER")
        self.db_password = os.getenv("DB_PASSWORD")

        # GitHub - multi-org support
        github_orgs_str = os.getenv("GITHUB_ORGS", "opentelekomcloud-docs")
        self.github_orgs = [org.strip() for org in github_orgs_str.split(',')]
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.github_fallback_token = os.getenv("GITHUB_FALLBACK_TOKEN")
        self.github_api_url = os.getenv("GITHUB_API_URL")

        # Jira - certificate auth
        self.jira_api_token = os.getenv("JIRA_TOKEN")
        self.jira_api_url = os.getenv("JIRA_API_URL")
        self.jira_cert_path = os.getenv("JIRA_CERT_PATH")
        self.jira_key_path = os.getenv("JIRA_KEY_PATH")

        # Gitea for affected locations
        base_gitea = os.getenv("BASE_GITEA_URL")
        gitea_path = "/repos/infra/otc-metadata-rework/contents/otc_metadata/data/cloud_environments/"
        self.gitea_url_envs = f"{base_gitea}{gitea_path}"

        # Master Components from Vault
        self.deh = os.getenv("DEH")
        self.asg = os.getenv("ASG")
        self.ecs = os.getenv("ECS")
        self.ims = os.getenv("IMS")
        self.bms = os.getenv("BMS")
        self.rds = os.getenv("RDS")
        self.opengauss = os.getenv("OPENGAUSS")
        self.geminidb = os.getenv("GEMINIDB")
        self.mysql = os.getenv("MYSQL")
        self.drs = os.getenv("DRS")
        self.das = os.getenv("DAS")
        self.ddm = os.getenv("DDM")
        self.dds = os.getenv("DDS")

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
            # Sanitize error to avoid password exposure
            self.logger.error("Database error (credentials hidden): %s", str(e).split('DETAIL')[0])
            raise
        finally:
            if conn:
                pool.putconn(conn)

    def connect_to_db(self, db_name):
        """Legacy method for backwards compatibility - returns connection from pool"""
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
    def __init__(self, env, timeout=60):
        self.api_url = env.jira_api_url
        self.token = env.jira_api_token
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


class GiteaClient:
    def __init__(self, env, timeout=10):
        self.base_url = env.gitea_url_envs
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def get_file_content(self, file_path):
        """Get decoded content of a file from Gitea."""
        try:
            file_url = f"{self.base_url}/{file_path}"
            response = session.get(file_url, timeout=self.timeout)
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

            response = session.get(url, timeout=self.timeout)
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
