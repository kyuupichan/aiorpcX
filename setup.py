import os.path
import re
import setuptools


def find_version(filename):
    with open(filename) as f:
        text = f.read()
    match = re.search(r"^_version_str = '(.*)'$", text, re.MULTILINE)
    if not match:
        raise RuntimeError('cannot find version')
    return match.group(1)


tld = os.path.abspath(os.path.dirname(__file__))
version = find_version(os.path.join(tld, 'aiorpcx', '__init__.py'))


setuptools.setup(
    name='aiorpcX',
    version=version,
    python_requires='>=3.6',
    install_requires=['websockets'],
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
