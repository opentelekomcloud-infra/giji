"""Config module for Giji"""

from .connections import Database, EnvVariables, GitHubClient, JiraClient, GiteaClient, Timer
env = EnvVariables()

REPO_TO_MASTER_COMPONENT = {
    "dedicated-host": env.deh,
    "auto-scaling": env.asg,
    "elastic-cloud-server": env.ecs,
    "image-management-service": env.ims,
    "bare-metal-server": env.bms,
    "relational-database-service": env.rds,
    "gaussdb-opengauss": env.opengauss,
    "geminidb": env.geminidb,
    "gaussdb-mysql": env.mysql,
    "data-replication-service": env.drs,
    "data-admin-service": env.das,
    "distributed-database-middleware": env.ddm,
    "document-database-service": env.dds
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
