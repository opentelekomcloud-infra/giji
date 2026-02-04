"""Constants and mappings - loaded from Vault environment variables"""
import os


# Master component mapping - values loaded from Vault via environment variables
REPO_TO_MASTER_COMPONENT = {
    "dedicated-host": os.getenv("DEH"),
    "auto-scaling": os.getenv("ASG"),
    "elastic-cloud-server": os.getenv("ECS"),
    "image-management-service": os.getenv("IMS"),
    "bare-metal-server": os.getenv("BMS"),
    "relational-database-service": os.getenv("RDS"),
    "gaussdb-opengauss": os.getenv("OPENGAUSS"),
    "geminidb": os.getenv("GEMINIDB"),
    "gaussdb-mysql": os.getenv("MYSQL"),
    "data-replication-service": os.getenv("DRS"),
    "data-admin-service": os.getenv("DAS"),
    "distributed-database-middleware": os.getenv("DDM"),
    "document-database-service": os.getenv("DDS")
}
