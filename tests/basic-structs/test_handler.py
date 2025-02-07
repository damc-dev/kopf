import pytest

from kopf.reactor.registries import ResourceHandler


def test_no_args():
    with pytest.raises(TypeError):
        ResourceHandler()


def test_all_args(mocker):
    fn = mocker.Mock()
    id = mocker.Mock()
    reason = mocker.Mock()
    field = mocker.Mock()
    timeout = mocker.Mock()
    initial = mocker.Mock()
    labels = mocker.Mock()
    annotations = mocker.Mock()
    handler = ResourceHandler(
        fn=fn,
        id=id,
        reason=reason,
        field=field,
        timeout=timeout,
        initial=initial,
        labels=labels,
        annotations=annotations,
    )
    assert handler.fn is fn
    assert handler.id is id
    assert handler.reason is reason
    assert handler.event is reason  # deprecated
    assert handler.field is field
    assert handler.timeout is timeout
    assert handler.initial is initial
    assert handler.labels is labels
    assert handler.annotations is annotations
