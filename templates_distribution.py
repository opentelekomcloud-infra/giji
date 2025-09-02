import requests
import os
import time
import logging
import psycopg2
import subprocess
import shutil
import tempfile

GITHUB_ORG = "opentelekomcloud-docs"

GITHUB_API_URL = "https://api.github.com"
GITHUB_TOKEN_BOT = os.getenv("GITHUB_BOT_TOKEN")

GITHUB_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN_BOT}",
    "Accept": "application/vnd.github.v3+json"
}

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT")
}

TARGET_SQUADS = ["Database Squad", "Compute Squad"]

LABELS_TO_CREATE = [
    {
        "name": "demand",
        "color": "EC9F36",
        "description": "Demand feature for Helpcenter"
    },
    {
        "name": "bug",
        "color": "d73a4a",
        "description": "Something isn't working"
    },
    {
        "name": "bulk",
        "color": "59110f",
        "description": "Issue has been created from blank"
    },
    {
        "name": "imported-to-jira",
        "color": "0075ca",
        "description": "Issue has been imported to Jira"
    }
]

TEMPLATE_FILES = [
    "bug-report.yml",
    "demand.yml",
    "config.yml"
]

TARGET_PATH = ".github/ISSUE_TEMPLATE"

BRANCH_NAME = "add_issue_templates"
PR_TITLE = "Add issue templates for bug reports and feature requests"
PR_BODY = """This PR adds standardized issue templates to improve issue reporting:

- **bug-report.yml**: Template for bug reports with structured fields
- **demand.yml**: Template for feature requests and demands
- **config.yml**: Configuration for issue template chooser

These templates will help users provide better structured information when creating issues."""

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def connect_to_database():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        logger.info("Successfully connected to database")
        return conn
    except psycopg2.Error as e:
        logger.error(f"Error connecting to database: {e}")
        raise


def get_repositories_from_db():
    conn = connect_to_database()
    repositories = []

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

        if not results:
            logger.warning(f"No repositories found for squads: {TARGET_SQUADS}")
            return []

        for repository, squad, title in results:
            repositories.append({
                'name': repository,
                'squad': squad,
                'title': title
            })
            logger.info(f"Found repository: {repository} (Squad: {squad})")

        logger.info(f"Found {len(repositories)} repositories from target squads")
        return repositories

    except psycopg2.Error as e:
        logger.error(f"Error querying database: {e}")
        raise
    finally:
        conn.close()


def check_repository_exists(repo_name):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}"
    response = requests.get(url, headers=GITHUB_HEADERS)

    if response.status_code == 200:
        return True
    elif response.status_code == 404:
        return False
    else:
        logger.warning(f"Error checking repository {repo_name}: {response.status_code}")
        return False


def create_label_in_repo(repo_name, label_config):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/labels"

    response = requests.post(url, json=label_config, headers=GITHUB_HEADERS)

    if response.status_code == 201:
        logger.info(f"{repo_name} - label '{label_config['name']}' created successfully")
        return True
    elif response.status_code == 422:
        error_data = response.json()
        if "already_exists" in error_data.get("message", "").lower():
            logger.info(f"{repo_name} - label '{label_config['name']}' already exists")
            return True
        else:
            logger.error(
                f"{repo_name} - error creating label '{label_config['name']}':"
                f" {error_data.get('message', 'Unknown error')}")
            return False
    else:
        try:
            error_msg = response.json().get("message", response.text)
        except (ValueError, KeyError):
            error_msg = response.text
        logger.error(
            f"{repo_name} - error {response.status_code} creating label '{label_config['name']}': {error_msg}")
        return False


def create_labels_for_repo(repo_name):
    logger.info(f"Creating labels in {repo_name}...")
    success_count = 0

    for label_config in LABELS_TO_CREATE:
        if create_label_in_repo(repo_name, label_config):
            success_count += 1
        time.sleep(0.3)

    return success_count == len(LABELS_TO_CREATE)


def run_git_command(command, cwd=None, check=True):
    try:
        result = subprocess.run(command, cwd=cwd, check=check, capture_output=True, text=True)
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {' '.join(command)}")
        logger.error(f"Error: {e.stderr}")
        raise


def check_pr_exists(repo_name, branch_name):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/pulls"
    params = {
        "state": "open",
        "head": f"{GITHUB_ORG}:{branch_name}"
    }

    response = requests.get(url, headers=GITHUB_HEADERS, params=params)
    if response.status_code == 200:
        pulls = response.json()
        return len(pulls) > 0, pulls[0] if pulls else None
    return False, None


def create_pull_request(repo_name, branch_name, title, body):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}/pulls"

    data = {
        "title": title,
        "body": body,
        "head": branch_name,
        "base": "main"
    }

    response = requests.post(url, json=data, headers=GITHUB_HEADERS)

    if response.status_code == 201:
        pr_data = response.json()
        logger.info(f"Created PR #{pr_data['number']}: {pr_data['html_url']}")
        return True, pr_data
    else:
        try:
            error_msg = response.json().get("message", response.text)
        except (ValueError, KeyError):
            error_msg = response.text
        logger.error(f"Failed to create PR: {response.status_code} {error_msg}")
        return False, None


def get_default_branch(repo_name):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{repo_name}"
    response = requests.get(url, headers=GITHUB_HEADERS)

    if response.status_code == 200:
        return response.json().get("default_branch", "main")
    return "main"


def process_repository_templates(repo_name):
    with tempfile.TemporaryDirectory() as temp_dir:
        repo_path = os.path.join(temp_dir, repo_name)
        repo_url = f"https://{GITHUB_TOKEN_BOT}@github.com/{GITHUB_ORG}/{repo_name}.git"

        try:
            pr_exists, existing_pr = check_pr_exists(repo_name, BRANCH_NAME)
            if pr_exists:
                logger.info(f"PR already exists for {repo_name}: {existing_pr['html_url']}")
                return True

            default_branch = get_default_branch(repo_name)
            logger.info(f"Default branch for {repo_name}: {default_branch}")

            logger.info(f"Cloning {repo_name}...")
            run_git_command(["git", "clone", repo_url, repo_path])

            run_git_command(["git", "config", "user.name", "GitHub Actions"], cwd=repo_path)
            run_git_command(["git", "config", "user.email", "noreply@github.com"], cwd=repo_path)

            logger.info(f"Switching to {default_branch} and pulling latest changes...")
            run_git_command(["git", "checkout", default_branch], cwd=repo_path)
            run_git_command(["git", "pull", "origin", default_branch], cwd=repo_path)

            logger.info(f"Checking if branch {BRANCH_NAME} exists...")

            result = run_git_command(["git", "branch", "-D", BRANCH_NAME], cwd=repo_path, check=False)
            if result.returncode == 0:
                logger.info(f"Deleted existing local branch {BRANCH_NAME}")

            result = run_git_command(["git", "push", "origin", "--delete", BRANCH_NAME], cwd=repo_path,
                                     check=False)
            if result.returncode == 0:
                logger.info(f"Deleted existing remote branch {BRANCH_NAME}")

            logger.info(f"Creating fresh branch {BRANCH_NAME} from {default_branch}...")
            run_git_command(["git", "checkout", "-b", BRANCH_NAME], cwd=repo_path)

            target_dir = os.path.join(repo_path, TARGET_PATH)
            os.makedirs(target_dir, exist_ok=True)
            logger.info(f"Created directory: {TARGET_PATH}")

            copied_files = []
            for template_file in TEMPLATE_FILES:
                if not os.path.exists(template_file):
                    logger.error(f"Template file not found: {template_file}")
                    continue

                destination = os.path.join(target_dir, template_file)
                shutil.copy2(template_file, destination)
                copied_files.append(template_file)
                logger.info(f"Copied: {template_file}")

            if not copied_files:
                logger.error(f"No template files copied to {repo_name}")
                return False

            run_git_command(["git", "add", TARGET_PATH], cwd=repo_path)

            result = run_git_command(["git", "status", "--porcelain"], cwd=repo_path)
            if not result.stdout.strip():
                logger.info(f"No changes to commit in {repo_name}")
                return True

            commit_message = f"Add issue templates\n\nAdded templates: {', '.join(copied_files)}"
            run_git_command(["git", "commit", "-m", commit_message], cwd=repo_path)
            logger.info("Committed changes")

            logger.info(f"Pushing branch {BRANCH_NAME}...")
            run_git_command(["git", "push", "origin", BRANCH_NAME], cwd=repo_path)

            logger.info("Creating pull request...")
            success, pr_data = create_pull_request(repo_name, BRANCH_NAME, PR_TITLE, PR_BODY)

            return success

        except Exception as e:
            logger.error(f"Error processing repository {repo_name}: {str(e)}")
            return False


def check_template_files_exist():
    missing_files = []

    for template_file in TEMPLATE_FILES:
        if not os.path.exists(template_file):
            missing_files.append(template_file)

    if missing_files:
        logger.error(f"Missing template files: {', '.join(missing_files)}")
        logger.error("Please ensure all template files are in the current directory:")
        for file in TEMPLATE_FILES:
            logger.error(f"  - {file}")
        return False

    logger.info("All template files found locally")
    return True


def check_git_available():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        logger.info("Git is available")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("Git is not available in PATH")
        return False


def check_environment_variables():
    missing_vars = []

    if not os.getenv("GITHUB_TOKEN_BOT"):
        missing_vars.append("GITHUB_TOKEN_BOT")

    db_vars = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    for var in db_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please set these variables before running the script")
        return False

    logger.info("Environment variables: OK")
    return True


def main():
    logger.info("=" * 80)
    logger.info("GitHub Repository Setup Script for Squads (Labels + PR Templates)")
    logger.info("=" * 80)
    logger.info(f"Target organization: {GITHUB_ORG}")
    logger.info(f"Target squads: {', '.join(TARGET_SQUADS)}")
    logger.info(f"Labels to create: {', '.join([label['name'] for label in LABELS_TO_CREATE])}")
    logger.info(f"Template files to copy: {', '.join(TEMPLATE_FILES)}")
    logger.info(f"Branch name for PRs: {BRANCH_NAME}")
    logger.info("=" * 80)

    if not check_environment_variables():
        return

    if not check_template_files_exist():
        return

    if not check_git_available():
        return

    try:
        logger.info("Fetching repositories from database...")
        repositories = get_repositories_from_db()

        if not repositories:
            logger.error("No repositories found in database for target squads")
            return

        total_repos = len(repositories)
        processed_repos = 0
        successful_labels = 0
        successful_prs = 0
        skipped_repos = 0

        for repo_info in repositories:
            repo_name = repo_info['name']
            squad = repo_info['squad']

            logger.info(f"Processing repository: {repo_name} (Squad: {squad})")
            logger.info("=" * 50)

            if not check_repository_exists(repo_name):
                logger.warning(f"Repository {repo_name} not found in organization {GITHUB_ORG}, skipping...")
                skipped_repos += 1
                continue

            processed_repos += 1

            if create_labels_for_repo(repo_name):
                successful_labels += 1
                logger.info(f"All labels created successfully in {repo_name}")
            else:
                logger.warning(f"Some labels failed to create in {repo_name}")

            logger.info(f"Processing template files for {repo_name}...")
            if process_repository_templates(repo_name):
                successful_prs += 1
                logger.info(f"Template PR created/updated successfully for {repo_name}")
            else:
                logger.warning(f"Template PR failed for {repo_name}")

            time.sleep(2)

        logger.info("FINAL SUMMARY:")
        logger.info("=" * 50)
        logger.info(f"  Total repositories found in DB: {total_repos}")
        logger.info(f"  Repositories processed: {processed_repos}")
        logger.info(f"  Repositories skipped (not found): {skipped_repos}")
        logger.info(f"  Repositories with all labels created: {successful_labels}")
        logger.info(f"  Repositories with template PRs created: {successful_prs}")

        if processed_repos > 0:
            label_success_rate = (successful_labels / processed_repos) * 100
            pr_success_rate = (successful_prs / processed_repos) * 100
            logger.info(f"  Label creation success rate: {label_success_rate:.1f}%")
            logger.info(f"  Template PR success rate: {pr_success_rate:.1f}%")

    except Exception as e:
        logger.error(f"CRITICAL ERROR: {str(e)}", exc_info=True)


if __name__ == "__main__":
    main()
