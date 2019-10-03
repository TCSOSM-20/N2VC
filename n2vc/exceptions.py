# Copyright 2019 Canonical Ltd.
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

class JujuCharmNotFound(Exception):
    """The Charm can't be found or is not readable."""


class JujuApplicationExists(Exception):
    """The Application already exists."""


class N2VCPrimitiveExecutionFailed(Exception):
    """Something failed while attempting to execute a primitive."""


class NetworkServiceDoesNotExist(Exception):
    """The Network Service being acted against does not exist."""


class PrimitiveDoesNotExist(Exception):
    """The Primitive being executed does not exist."""


class NoRouteToHost(Exception):
    """There was no route to the specified host."""


class AuthenticationFailed(Exception):
    """The authentication for the specified user failed."""

class InvalidCACertificate(Exception):
    """The CA Certificate is not valid."""