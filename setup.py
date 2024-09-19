import setuptools

with open("README.md", "r") as f:
    long_description = f.read()

setuptools.setup(
    name='keboola_streamlit',
    version='0.0.6',
    author="pandyandy",
    setup_requires=['pytest-runner', 'flake8'],
    tests_require=['pytest'],
    install_requires=[
        'pygelf',
        'pytz',
        'deprecated',
        'streamlit',
        'pandas',
        'kbcstorage'
    ],
    author_email='novakovadrea@gmail.com',
    description='A Python library that simplifies Keboola SAPI integration in Streamlit apps.',
    long_description=long_description,
    long_description_content_type="text/markdown",
    package_dir={'': 'src'},
    packages=setuptools.find_packages(where='src'),
    include_package_data=True,
    zip_safe=False,
    test_suite='tests',
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "Intended Audience :: Education",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Development Status :: 5 - Production/Stable"
    ],
    python_requires='>=3.7'
)