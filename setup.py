# Copyright 2016 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.

from setuptools import setup, find_packages

_description = 'OSM library implementing common interface towards VCA module'

setup(
    name='N2VC',
    version_command=('git describe --match v* --tags --long --dirty',
                     'pep440-git-full'),
    packages=find_packages(
        exclude=["*.tests", "*.tests.*", "tests.*", "tests"]),
    install_requires=[
        'juju==2.8.1',
        'paramiko',
        'pyasn1>=0.4.4',
        'kubernetes==10.0.1'
    ],
    include_package_data=True,
    maintainer='Adam Israel',
    maintainer_email='adam.israel@canonical.com',
    description=_description,
    url='',
    license='Apache 2',
    entry_points={
        'console_scripts': [
        ],
    },
)
