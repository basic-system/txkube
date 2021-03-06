# Copyright Least Authority Enterprises.
# See LICENSE for details.

"""
Integration between Eliot, eliottree, and testtools to provide easily
readable Eliot logs for failing tests.
"""

from io import StringIO

from fixtures import Fixture

from eliot import add_destinations, remove_destination
from eliottree import tasks_from_iterable, render_tasks

from testtools.content import Content
from testtools.content_type import UTF8_TEXT


def _eliottree(logs):
    """
    Render some Eliot log events into a tree-like string.

    :param list[dict] logs: The Eliot log events to render.  These should be
        dicts like those passed to an Eliot destination.

    :return unicode: The rendered string.
    """
    tasks = tasks_from_iterable(logs)

    out = StringIO()
    render_tasks(
        write=out.write,
        tasks=tasks,
        field_limit=0,
    )
    return out.getvalue()



class CaptureEliotLogs(Fixture):
    """
    A fixture which captures Eliot logs emitted while it is active and adds a
    detail which includes the (easily human-readable) "tree" rendering of
    those logs.
    """
    LOG_DETAIL_NAME = "eliot-log"

    # Unusually, the Fixtures convention is that underscore prefixed methods
    # are not private.  Instead, they're more like internal hooks.  It is
    # intended that application code override these methods you might
    # otherwise expect are private.
    def _setUp(self):
        self.logs = []
        add_destinations(self.logs.append)
        self.addCleanup(lambda: remove_destination(self.logs.append))
        self.addDetail(
            self.LOG_DETAIL_NAME,
            Content(
                UTF8_TEXT,
                # Safeguard the logs against _tearDown.  Capture the list
                # object in the lambda's defaults.
                lambda logs=self.logs: [_eliottree(logs).encode("utf-8")],
            ),
        )
