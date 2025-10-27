"""Setup configuration for Giji package."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="giji",
    version="0.1.0",
    author="OpenTelekomCloud Infrastructure Team",
    description="GitHub to Jira issue importing tool",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/opentelekomcloud-infra/giji",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.12",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "giji=giji.cli.main:main",
        ],
    },
)
