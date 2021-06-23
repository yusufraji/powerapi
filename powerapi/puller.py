# Copyright (c) 2018, INRIA
# Copyright (c) 2018, University of Lille
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import logging
import asyncio
import time
from datetime import timedelta
from threading import Thread
from thespian.actors import ActorSystem, ActorAddress, ActorExitRequest


from powerapi.actor import TimedActor, InitializationException
from powerapi.exception import PowerAPIException
from powerapi.database import BaseDB, DBError, SocketDB
from powerapi.filter import Filter, RouterWithoutRuleException
from powerapi.report.report import DeserializationFail
from powerapi.report_model import ReportModel
from powerapi.report_model.report_model import BadInputData
from powerapi.message import PullerStartMessage


class PullerActor(TimedActor):
    def __init__(self):
        TimedActor.__init__(self, 1)

        self.database = None
        self.report_filter = None
        self.report_model = None
        self.stream_mode = None
        self.database_it = None

        self._number_of_message_before_sleeping = 10

    def _initialization(self, start_message: PullerStartMessage):
        TimedActor._initialization(self, start_message)
        
        if not isinstance(start_message, PullerStartMessage):
            raise InitializationException('use PullerStartMessage instead of StartMessage')

        self.database = start_message.database
        self.report_filter = start_message.report_filter
        self.report_model = start_message.report_model
        self.stream_mode = start_message.stream_mode

        self._database_connection()
        if not self.report_filter.filters:
            raise InitializationException('filter without rules')


    def _database_connection(self):
        try:
            if not self.database.asynchrone:
                self.database.connect()
            else:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.loop.set_debug(enabled=True)
                logging.basicConfig(level=logging.DEBUG)
                # self.database.connect() ???
                self.loop.run_until_complete(self.database.connect())
            self.database_it = self.database.iter(self.report_model, self.stream_mode)


        except DBError as error:
            raise InitializationException(error.msg)

    def _launch_task(self):
        """
        Initialize the database and connect all dispatcher to the
        socket_interface
        """
        for _ in range(self._number_of_message_before_sleeping):
            try:
                report = self._pull_database()
                dispatchers = self.report_filter.route(report)
                for dispatcher in dispatchers:
                    self.send(dispatcher, report)

            except StopIteration:
                if self.stream_mode:
                    self.wakeupAfter(self._time_interval)
                else:
                    self.send(self.myAddress, ActorExitRequest())
                    return
            except (BadInputData, DeserializationFail):
                pass
        self.wakeupAfter(self._time_interval)


    def _pull_database(self):
        if self.database.asynchrone:
            report = self.loop.run_until_complete(self.database_it.__anext__())
            if report is not None:
                return report
            else:
                raise StopIteration()
        else:
            return next(self.database_it)
