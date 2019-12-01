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


class NotImplemented(Exception):
    """The method is not implemented."""


class N2VCException(Exception):
    """
    N2VC exception base class
    """

    def __init__(self, message: str = ''):
        Exception.__init__(self, message)
        self.message = message

    def __str__(self):
        return self.message

    def __repr__(self):
        return '{}({})'.format(type(self), self.message)


class N2VCBadArgumentsException(N2VCException):
    """
    Bad argument values exception
    """

    def __init__(self, message: str = '', bad_args: list = None):
        N2VCException.__init__(self, message=message)
        self.bad_args = bad_args

    def __str__(self):
        return '<{}> Bad arguments: {} -> {}'.format(type(self), super().__str__(), self.bad_args)


class N2VCConnectionException(N2VCException):
    """
    Error connecting to VCA
    """

    def __init__(self, message: str = '', url: str = None):
        N2VCException.__init__(self, message=message)
        self.url = url

    def __str__(self):
        return '<{}> Connection to {} failed: {}'.format(type(self), self.url, super().__str__())


class N2VCTimeoutException(N2VCException):
    """
    Timeout
    """

    def __init__(self, message: str = '', timeout: str = ''):
        N2VCException.__init__(self, message=message)
        self.timeout = timeout

    def __str__(self):
        return '<{}> {} timeout: {}'.format(type(self), self.timeout, super().__str__())


class N2VCExecutionException(N2VCException):
    """
    Error executing primitive
    """

    def __init__(self, message: str = '', primitive_name: str = ''):
        N2VCException.__init__(self, message=message)
        self.primitive_name = primitive_name

    def __str__(self):
        return '<{}> Error executing primitive {} failed: {}'.format(type(self), self.primitive_name, super().__str__())


class N2VCInvalidCertificate(N2VCException):
    """
    Invalid certificate
    """

    def __init__(self, message: str = ''):
        N2VCException.__init__(self, message=message)

    def __str__(self):
        return '<{}> Invalid certificate: {}'.format(type(self), super().__str__())
