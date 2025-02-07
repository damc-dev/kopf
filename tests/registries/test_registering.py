import collections.abc

from kopf import ResourceRegistry, OperatorRegistry


# Used in the tests. Must be global-scoped, or its qualname will be affected.
def some_fn():
    pass


def test_simple_registry_via_iter(mocker):
    cause = mocker.Mock(event=None, diff=None)

    registry = ResourceRegistry()
    iterator = registry.iter_resource_changing_handlers(cause)

    assert isinstance(iterator, collections.abc.Iterator)
    assert not isinstance(iterator, collections.abc.Collection)
    assert not isinstance(iterator, collections.abc.Container)
    assert not isinstance(iterator, (list, tuple))

    handlers = list(iterator)
    assert not handlers


def test_simple_registry_via_list(mocker):
    cause = mocker.Mock(event=None, diff=None)

    registry = ResourceRegistry()
    handlers = registry.get_resource_changing_handlers(cause)

    assert isinstance(handlers, collections.abc.Iterable)
    assert isinstance(handlers, collections.abc.Container)
    assert isinstance(handlers, collections.abc.Collection)
    assert not handlers


def test_simple_registry_with_minimal_signature(mocker):
    cause = mocker.Mock(event=None, diff=None)

    registry = ResourceRegistry()
    registry.register(some_fn)
    handlers = registry.get_resource_changing_handlers(cause)

    assert len(handlers) == 1
    assert handlers[0].fn is some_fn


def test_global_registry_via_iter(mocker, resource):
    cause = mocker.Mock(resource=resource, event=None, diff=None)

    registry = OperatorRegistry()
    iterator = registry.iter_resource_changing_handlers(cause)

    assert isinstance(iterator, collections.abc.Iterator)
    assert not isinstance(iterator, collections.abc.Collection)
    assert not isinstance(iterator, collections.abc.Container)
    assert not isinstance(iterator, (list, tuple))

    handlers = list(iterator)
    assert not handlers


def test_global_registry_via_list(mocker, resource):
    cause = mocker.Mock(resource=resource, event=None, diff=None)

    registry = OperatorRegistry()
    handlers = registry.get_resource_changing_handlers(cause)

    assert isinstance(handlers, collections.abc.Iterable)
    assert isinstance(handlers, collections.abc.Container)
    assert isinstance(handlers, collections.abc.Collection)
    assert not handlers


def test_global_registry_with_minimal_signature(mocker, resource):
    cause = mocker.Mock(resource=resource, event=None, diff=None)

    registry = OperatorRegistry()
    registry.register_resource_changing_handler(resource.group, resource.version, resource.plural, some_fn)
    handlers = registry.get_resource_changing_handlers(cause)

    assert len(handlers) == 1
    assert handlers[0].fn is some_fn

