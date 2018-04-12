import setuptools

from aiorpcx import _version_str as version


setuptools.setup(
    name='aiorpcX',
    version=version,
    python_requires='>=3.6',
    packages=['aiorpcx'],
    description='Generic async RPC implementation, including JSON-RPC',
    author='Neil Booth',
    author_email='kyuupichan@gmail.com',
    license='MIT Licence',
    url='https://github.com/kyuupichan/aiorpcX',
    download_url=('https://github.com/kyuupichan/aiorpcX/archive/'
                  f'{version}.tar.gz'),
    long_description=(
        'Transport, protocol and framing-independent async RPC '
        'client and server implementation.  '
    ),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Framework :: AsyncIO',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        "Programming Language :: Python :: 3.6",
        'Topic :: Internet',
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
