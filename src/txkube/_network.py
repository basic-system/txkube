# Copyright Least Authority Enterprises.
# See LICENSE for details.

"""
A Kubernetes client which uses Twisted to interact with Kubernetes
via HTTP.
"""

from json import loads, dumps

from zope.interface import implementer

import attr
from attr import validators

from twisted.python.reflect import namedAny
from twisted.python.url import URL
from twisted.python.failure import Failure

from twisted.internet.defer import succeed

from twisted.web.iweb import IBodyProducer, IAgent
from twisted.web.http import OK, CREATED
from twisted.web.client import Agent, readBody

from eliot import start_action
from eliot.twisted import DeferredContext

from . import (
    IObject, IKubernetes, IKubernetesClient, KubernetesError,
    object_from_raw,
)

def network_kubernetes(**kw):
    return _NetworkKubernetes(**kw)


# It would be simpler to use FileBodyProducer(BytesIO(...)) but:
#
#  - https://twistedmatrix.com/trac/ticket/9003
#  - https://github.com/twisted/treq/issues/161
@implementer(IBodyProducer)
@attr.s(frozen=True)
class _BytesProducer(object):
    _data = attr.ib(validator=validators.instance_of(bytes), repr=False)

    @property
    def length(self):
        return len(self._data)

    def startProducing(self, consumer):
        consumer.write(self._data)
        return succeed(None)

    def stopProducing(self):
        pass

    def pauseProducing(self):
        pass

    def resumeProducing(self):
        pass



@implementer(IKubernetesClient)
@attr.s(frozen=True)
class _NetworkClient(object):
    _apiVersion = u"v1"

    kubernetes = attr.ib(validator=validators.provides(IKubernetes))
    agent = attr.ib(validator=validators.provides(IAgent))

    def _request(self, method, url, headers=None, bodyProducer=None):
        action = start_action(
            action_type=u"network-client:request",
            method=method,
            url=url.asText(),
        )
        with action.context():
            d = self.agent.request(
                method, url.asText().encode("ascii"), headers, bodyProducer,
            )
            return DeferredContext(d).addActionFinish()


    def _get(self, url):
        return self._request(b"GET", url)


    def _delete(self, url):
        return self._request(b"DELETE", url)


    def _post(self, url, obj):
        return self._request(
            b"POST", url, bodyProducer=_BytesProducer(dumps(obj)),
        )


    def create(self, obj):
        """
        Issue a I{POST} to create the given object.
        """
        action = start_action(
            action_type=u"network-client:create",
        )
        with action.context():
            url = self.kubernetes.base_url.child(*collection_location(obj))
            document = obj.to_raw()
            action.add_success_fields(submitted_object=document)
            d = DeferredContext(self._post(url, document))
            d.addCallback(check_status, (CREATED,))
            d.addCallback(readBody)
            d.addCallback(loads)
            d.addCallback(log_response_object, action)
            d.addCallback(object_from_raw)
            return d.addActionFinish()


    def get(self, obj):
        """
        Issue a I{GET} to retrieve the given object.

        The object must have identifying metadata such as a namespace and a
        name but other fields are ignored.
        """
        action = start_action(
            action_type=u"network-client:get",
            kind=obj.kind,
            name=obj.metadata.name,
            namespace=getattr(obj.metadata, "namespace", None),
        )
        with action.context():
            url = self.kubernetes.base_url.child(*object_location(obj))
            d = DeferredContext(self._get(url))
            d.addCallback(check_status, (OK,))
            d.addCallback(readBody)
            d.addCallback(loads)
            d.addCallback(log_response_object, action)
            d.addCallback(object_from_raw)
            return d.addActionFinish()


    def delete(self, obj):
        """
        Issue a I{DELETE} to delete the given object.
        """
        action = start_action(
            action_type=u"network-client:delete",
            kind=obj.kind,
            name=obj.metadata.name,
            namespace=getattr(obj.metadata, "namespace", None),
        )
        with action.context():
            url = self.kubernetes.base_url.child(*object_location(obj))
            d = DeferredContext(self._delete(url))
            d.addCallback(check_status, (OK,))
            d.addCallback(readBody)
            d.addCallback(lambda raw: None)
            return d.addActionFinish()


    def list(self, kind):
        """
        Issue a I{GET} to retrieve objects of a given kind.
        """
        action = start_action(
            action_type=u"network-client:list",
            kind=kind,
        )
        with action.context():
            url = self.kubernetes.base_url.child(*collection_location(kind))
            d = DeferredContext(self._get(url))
            d.addCallback(check_status, (OK,))
            d.addCallback(readBody)
            d.addCallback(loads)
            d.addCallback(object_from_raw)
            return d.addActionFinish()



def object_location(obj):
    """
    Get the URL for a specific object.

    :param IObject obj: The object the URL for which to get.

    :return tuple[unicode]: Some path segments to stick on to a base URL top
        construct the location for the given object.
    """
    return collection_location(obj) + (obj.metadata.name,)



def collection_location(obj):
    """
    Get the URL for the collection of objects like ``obj``.

    :param obj: Either a type representing a Kubernetes object kind or an
        instance of such a type.

    :return tuple[unicode]: Some path segments to stick on to a base URL to
        construct the location of the collection of objects like the one
        given.
    """
    # TODO kind is not part of IObjectLoader and we should really be loading
    # apiVersion off of this object too.
    kind = obj.kind

    collection = kind.lower() + u"s"

    if IObject.providedBy(obj):
        # Actual objects *could* have a namespace...
        namespace = obj.metadata.namespace
    else:
        # Types representing a kind couldn't possible.
        namespace = None

    if namespace is None:
        # If there's no namespace, look in the un-namespaced area.
        return (u"api", u"v1", collection)

    # If there is, great, look there.
    return (u"api", u"v1", u"namespaces", namespace, collection)


@implementer(IKubernetes)
@attr.s(frozen=True)
class _NetworkKubernetes(object):
    """
    ``_NetworkKubernetes`` knows the location of a particular
    Kubernetes deployment and gives out clients which speak to that
    deployment.
    """
    base_url = attr.ib(validator=validators.instance_of(URL))
    _agent = attr.ib(
        default=attr.Factory(lambda: Agent(namedAny("twisted.internet.reactor"))),
    )

    def client(self):
        return _NetworkClient(self, self._agent)



def log_response_object(document, action):
    """
    Emit an Eliot log event belonging to the given action describing the given
    response.

    :param document: Anything Eliot loggable (but presumably a parsed
        Kubernetes response document).

    :param action: The Eliot action to which to attach the event.

    :return: ``document``
    """
    action.add_success_fields(response_object=document)
    return document


def check_status(response, expected):
    if response.code not in expected:
        d = KubernetesError.from_response(response)
        d.addCallback(Failure)
        return d
    return response
