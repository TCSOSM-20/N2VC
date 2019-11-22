##
# Copyright 2019 Telefonica Investigacion y Desarrollo, S.A.U.
# This file is part of OSM
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For those usages not covered by the Apache License, Version 2.0 please
# contact with: nfvlabs@tid.es
##


import logging
import asyncio
import time
import inspect
import datetime
import threading    # only for logging purposes (not for using threads)


class Loggable:

    def __init__(
            self,
            log,
            log_to_console: bool = False,
            prefix: str = ''
    ):

        self._last_log_time = None   # used for time increment in logging
        self._log_to_console = log_to_console
        self._prefix = prefix
        if log is not None:
            self.log = log
        else:
            self.log = logging.getLogger(__name__)

    def debug(self, msg: str):
        self._log_msg(log_level='DEBUG', msg=msg)

    def info(self, msg: str):
        self._log_msg(log_level='INFO', msg=msg)

    def warning(self, msg: str):
        self._log_msg(log_level='WARNING', msg=msg)

    def error(self, msg: str):
        self._log_msg(log_level='ERROR', msg=msg)

    def critical(self, msg: str):
        self._log_msg(log_level='CRITICAL', msg=msg)

    ##################################################################################################

    def _log_msg(self, log_level: str, msg: str):
        """Generic log method"""
        msg = self._format_log(
            log_level=log_level,
            msg=msg,
            obj=self,
            level=3,
            include_path=False,
            include_thread=False,
            include_coroutine=True
        )
        if self._log_to_console:
            print(msg)
        else:
            if self.log is not None:
                if log_level == 'DEBUG':
                    self.log.debug(msg)
                elif log_level == 'INFO':
                    self.log.info(msg)
                elif log_level == 'WARNING':
                    self.log.warning(msg)
                elif log_level == 'ERROR':
                    self.log.error(msg)
                elif log_level == 'CRITICAL':
                    self.log.critical(msg)

    def _format_log(
            self,
            log_level: str,
            msg: str = '',
            obj: object = None,
            level: int = None,
            include_path: bool = False,
            include_thread: bool = False,
            include_coroutine: bool = True
    ) -> str:

        # time increment from last log
        now = time.perf_counter()
        if self._last_log_time is None:
            time_str = ' (+0.000)'
        else:
            diff = round(now - self._last_log_time, 3)
            time_str = ' (+{})'.format(diff)
        self._last_log_time = now

        if level is None:
            level = 1

        # stack info
        fi = inspect.stack()[level]
        filename = fi.filename
        func = fi.function
        lineno = fi.lineno
        # filename without path
        if not include_path:
            i = filename.rfind('/')
            if i > 0:
                filename = filename[i+1:]

        # datetime
        dt = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        dt = dt + time_str
        dt = time_str       # logger already shows datetime

        # current thread
        if include_thread:
            thread_name = 'th:{}'.format(threading.current_thread().getName())
        else:
            thread_name = ''

        # current coroutine

        coroutine_id = ''
        if include_coroutine:
            try:
                if asyncio.Task.current_task() is not None:
                    def print_cor_name(c):
                        import inspect
                        try:
                            for m in inspect.getmembers(c):
                                if m[0] == '__name__':
                                    return m[1]
                        except Exception:
                            pass
                    coro = asyncio.Task.current_task()._coro
                    coroutine_id = 'coro-{} {}()'.format(hex(id(coro))[2:], print_cor_name(coro))
            except Exception:
                coroutine_id = ''

        # classname
        if obj is not None:
            obj_type = obj.__class__.__name__  # type: str
            log_msg = \
                '{} {} {} {} {}::{}.{}():{}\n{}'\
                .format(self._prefix, dt, thread_name, coroutine_id, filename, obj_type, func, lineno, str(msg))
        else:
            log_msg = \
                '{} {} {} {} {}::{}():{}\n{}'\
                .format(self._prefix, dt, thread_name, coroutine_id, filename, func, lineno, str(msg))

        return log_msg
