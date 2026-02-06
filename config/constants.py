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

# Test category IDS - values loaded from Vault
TEST_CATEGORY_IDS = {
    "QA": os.getenv("QA"),
    "UAT": os.getenv("UAT"),
    "Security": os.getenv("SEC")
}

# Template fields IDs for mapping
template_field_map = {
    "master_component": os.getenv("master_component"),
    "users_impact": os.getenv("users_impact"),
    "affected_locations": os.getenv("affected_locations"),
    "test_category": os.getenv("test_category"),
    "priority": os.getenv("priority"),
    "bug_type": os.getenv("bug_type"),
    "affected_areas": os.getenv("affected_areas"),
    "estimated_effort": os.getenv("estimated_effort"),
    "tier": os.getenv("tier"),
    "pays_into": os.getenv("pays_into"),
    "description": os.getenv("description")
}
