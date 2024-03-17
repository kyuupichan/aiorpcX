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
    version=version,
    python_requires='>=3.8',
    packages=['aiorpcx'],
    # Tell setuptools to include data files specified by MANIFEST.in.
    include_package_data=True,
    download_url=('https://github.com/kyuupichan/aiorpcX/archive/'
                  f'{version}.tar.gz'),
    long_description=(
        'Transport, protocol and framing-independent async RPC '
        'client and server implementation.  '
    ),
)
