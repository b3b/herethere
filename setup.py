from setuptools import setup, convert_path

main_ns = {}
with open(convert_path("herethere/herethere_version.py")) as ver_file:
    exec(ver_file.read(), main_ns)
    version = main_ns["__version__"]

with open(convert_path("README.rst")) as readme_file:
    long_description = readme_file.read()


setup(
    name="herethere",
    version=main_ns["__version__"],
    packages=[
        "herethere",
        "herethere.everywhere",
        "herethere.here",
        "herethere.magic",
        "herethere.there",
        "herethere.there.commands",
    ],
    description="herethere",
    long_description=long_description,
    long_description_content_type="text/x-rst",
    author="b3b",
    author_email="ash.b3b@gmail.com",
    install_requires=[
        "asyncssh",
        "click",
        "python-dotenv",
    ],
    extras_require={
        "magic": [
            "ipython",
            "ipywidgets",
            "nest_asyncio",
        ],
        "dev": [
            "black",
            "codecov",
            "docutils",
            "flake8",
            "pylint",
            "pytest",
            "pytest-asyncio",
            "pytest-cov",
            "pytest-mock",
        ],
    },
    url="https://github.com/b3b/herethere",
    # https://pypi.org/classifiers/
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
    keywords="SSH magic ipython jupyter",
    license="MIT",
    zip_safe=False,
)
