from setuptools import setup, find_packages

setup(
    name="destiny-vault-tool",
    version="0.1.0",
    description="A tool for managing and analyzing Destiny 2 vault items",
    author="",
    packages=find_packages(),
    install_requires=[
        "requests",
        "colorama",
        "rich",
        "beautifulsoup4",
        "lxml",
        "pyyaml",
    ],
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "d2vault=src.cli:main",
        ],
    },
)
