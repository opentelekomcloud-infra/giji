"""Config module for Giji"""

from .connections import Database, EnvVariables, GitHubClient, JiraClient, GiteaClient, Timer

REPO_TO_MASTER_COMPONENT = {
    "dedicated-host": "OCH-1027707",
    "auto-scaling": "OCH-1027753",
    "elastic-cloud-server": "OCH-1027712",
    "image-management-service": "OCH-1568488",
    "bare-metal-server": "OCH-1027668",
    "relational-database-service": "OCH-1027734",
    "gaussdb-opengauss": "OCH-1027718",
    "geminidb": "OCH-1027721",
    "gaussdb-mysql": "OCH-2332896",
    "data-replication-service": "OCH-1027709",
    "data-admin-service": "OCH-1027698",
    "distributed-database-middleware": "OCH-1278335",
    "document-database-service": "OCH-1027703"
}

__all__ = [
    'Database',
    'EnvVariables',
    'GitHubClient',
    'JiraClient',
    'GiteaClient',
    'Timer',
    'REPO_TO_MASTER_COMPONENT'
]
