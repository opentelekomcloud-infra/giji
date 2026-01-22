#!/usr/bin/env python3
"""GitHub Labels Creator for repositories from database."""

import logging
import time

import psycopg2

from config import Database, EnvVariables, GitHubClient

env_vars = EnvVariables()
database = Database(env_vars)
github_client = GitHubClient(env_vars)

TARGET_SQUADS = ["Database Squad", "Compute Squad"]

LABELS_TO_CREATE = [
    {
        "name": "bulk",
        "color": "59110f",
        "description": "Issue has been created from blank"
    },
    {
        "name": "bug",
        "color": "d73a4a",
        "description": "Something isn't working"
    },
    {
        "name": "demand",
        "color": "ec9f36",
        "description": "Demand feature for Helpcenter"
    },
    {
        "name": "documentation_bug",
        "color": "fe5611",
        "description": "Bugs in documentation"
    },
    {
        "name": "imported-to-jira",
        "color": "0075ca",
        "description": "Issue has been imported to Jira"
    }
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_repositories_from_db():
    """Get repositories from target squads."""
    conn = database.connect_to_db(env_vars.db_csv)
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

        for repository, squad, title in results:
            repositories.append(repository)

        return repositories
    except psycopg2.Error as e:
        logger.error("Error querying database: %s", e)
        raise
    finally:
        conn.close()


def main():
    logger.info("=" * 60)
    logger.info("GitHub Labels Creator")
    logger.info("=" * 60)
    logger.info("Target squads: %s", ', '.join(TARGET_SQUADS))
    logger.info("=" * 60)

    try:
        repositories = get_repositories_from_db()
        if not repositories:
            logger.error("No repositories found")
            return

        logger.info("Found %d repositories", len(repositories))

        total_successful = 0
        total_failed = 0

        for github_org in env_vars.github_orgs:
            logger.info("Processing organization: %s", github_org)

            # Check permissions on first repo as test
            if repositories:
                test_repo = repositories[0]
                has_permissions = github_client.check_repo_permissions(github_org, test_repo)
                if not has_permissions:
                    logger.warning("Limited permissions detected for %s. Some operations may fail.", github_org)

            for repo_name in repositories:
                logger.info("Processing %s/%s...", github_org, repo_name)

                repo_success = 0
                for label_config in LABELS_TO_CREATE:
                    success, status = github_client.create_label(github_org, repo_name, label_config)
                    if success:
                        repo_success += 1
                    time.sleep(0.3)

                if repo_success == len(LABELS_TO_CREATE):
                    total_successful += 1
                else:
                    total_failed += 1
                    logger.warning("%s/%s - %d/%d labels processed",
                                  github_org, repo_name, repo_success, len(LABELS_TO_CREATE))

        # Summary
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        logger.info("Total organizations: %d", len(env_vars.github_orgs))
        logger.info("Fully successful: %d", total_successful)
        logger.info("Had issues: %d", total_failed)

    except Exception as e:
        logger.error("Critical error: %s", e, exc_info=True)


if __name__ == "__main__":
    main()
