# Copyright (c) 2018, Neil Booth
#
# All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

__all__ = ('instantiate_coroutine', 'is_valid_hostname', 'classify_host')


import asyncio
from collections import namedtuple
from functools import partial
import inspect
from ipaddress import ip_address, IPv4Address, IPv6Address
import re
from socket import AF_INET, AF_INET6


def instantiate_coroutine(corofunc, args):
    if asyncio.iscoroutine(corofunc):
        if args != ():
            raise ValueError('args cannot be passed with a coroutine')
        return corofunc
    return corofunc(*args)


def is_async_call(func):
    '''inspect.iscoroutinefunction that looks through partials.'''
    while isinstance(func, partial):
        func = func.func
    return inspect.iscoroutinefunction(func)


# other_params: None means cannot be called with keyword arguments only
# any means any name is good
SignatureInfo = namedtuple('SignatureInfo', 'min_args max_args '
                           'required_names other_names')


def signature_info(func):
    params = inspect.signature(func).parameters
    min_args = max_args = 0
    required_names = []
    other_names = []
    no_names = False
    for p in params.values():
        if p.kind == p.POSITIONAL_OR_KEYWORD:
            max_args += 1
            if p.default is p.empty:
                min_args += 1
                required_names.append(p.name)
            else:
                other_names.append(p.name)
        elif p.kind == p.KEYWORD_ONLY:
            other_names.append(p.name)
        elif p.kind == p.VAR_POSITIONAL:
            max_args = None
        elif p.kind == p.VAR_KEYWORD:
            other_names = any
        elif p.kind == p.POSITIONAL_ONLY:
            max_args += 1
            if p.default is p.empty:
                min_args += 1
            no_names = True

    if no_names:
        other_names = None

    return SignatureInfo(min_args, max_args, required_names, other_names)


def check_task(logger, task):
    if not task.cancelled():
        try:
            task.result()
        except Exception:
            logger.error('task crashed: %r', task, exc_info=True)


# See http://stackoverflow.com/questions/2532053/validate-a-hostname-string
# Note underscores are valid in domain names, but strictly invalid in host
# names.  We ignore that distinction.
LABEL_REGEX = re.compile('^[a-z0-9_]([a-z0-9-_]{0,61}[a-z0-9_])?$', re.IGNORECASE)
NUMERIC_REGEX = re.compile('[0-9]+$')


def is_valid_hostname(hostname):
    '''Return True if hostname is valid, otherwise False.'''
    if not isinstance(hostname, str):
        raise TypeError('hostname must be a string')
    # strip exactly one dot from the right, if present
    if hostname and hostname[-1] == ".":
        hostname = hostname[:-1]
    if not hostname or len(hostname) > 253:
        return False
    labels = hostname.split('.')
    # the TLD must be not all-numeric
    if re.match(NUMERIC_REGEX, labels[-1]):
        return False
    return all(LABEL_REGEX.match(label) for label in labels)


def classify_host(host):
    '''Host is an IPv4Address, IPv6Address or a string.

    If an IPv4Address or IPv6Address return it.  Otherwise convert the string to an
    IPv4Address or IPv6Address object if possible and return it.  Otherwise return the
    original string if it is a valid hostname.

    Raise ValueError if a string cannot be interpreted as an IP address and it is not
    a valid hostname.
    '''
    if isinstance(host, (IPv4Address, IPv6Address)):
        return host
    if is_valid_hostname(host):
        return host
    return ip_address(host)
