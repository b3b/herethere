from setuptools import setup, convert_path

main_ns = {}
with open(convert_path('herethere/version.py')) as ver_file:
    exec(ver_file.read(), main_ns)

with open(convert_path('README.rst')) as readme_file:
    long_description = readme_file.read()


setup(
    name='herethere',
    version=main_ns['__version__'],
    packages=['herethere'],
    description='herethere',
    long_description=long_description,
    long_description_content_type='text/x-rst',
    author='b3b',
    author_email='ash.b3b@gmail.com',
    install_requires=[],
    extras_require={
        'dev': [
            'codecov',
            'docutils',
            'flake8',
            'pylint',
            'pytest',
            'pytest-cov',
            'pytest-mock',
        ],
    },
    url='https://github.com/b3b/ipython-herethere',
    # https://pypi.org/classifiers/
    classifiers=[
        'Development Status :: 1 - Planning',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
    ],
    keywords='android magic ipython jupyter',
    license='MIT',
    zip_safe=False,
)
