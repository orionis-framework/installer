from setuptools import setup, find_packages
from orionis_cli.api import OrionisFrameworkApi

metadata = OrionisFrameworkApi()

setup(
    name=metadata.getName(),
    version=metadata.getVersion(),
    description=metadata.getDescription(),
    long_description=open('README.md', encoding='utf-8').read(),
    long_description_content_type='text/markdown',
    author=metadata.getAuthor(),
    author_email=metadata.getAuthorEmail(),
    url=metadata.getUrl(),
    license=metadata.getLicense(),
    classifiers=metadata.getClassifiers(),
    keywords=metadata.getKeywords(),
    python_requires=metadata.getPythonVersion(),
    packages=find_packages(include=["orionis_cli*"]),
    package_dir={"": "."},
    include_package_data=True,
    install_requires=[
        "requests>=2.32.3",
        "rich>=13.9.4"
    ],
    entry_points={
        "console_scripts": [
            "orionis = orionis_cli.installer:setup"
        ]
    },
    test_suite="tests",
    zip_safe=True
)