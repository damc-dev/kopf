import asyncio

import pytest

import kopf
from kopf.reactor.causation import Reason, HANDLER_REASONS
from kopf.reactor.handling import resource_handler


@pytest.mark.parametrize('cause_type', HANDLER_REASONS)
async def test_1st_step_stores_progress_by_patching(
        registry, handlers, extrahandlers,
        resource, cause_mock, cause_type, k8s_mocked):
    name1 = f'{cause_type}_fn'
    name2 = f'{cause_type}_fn2'

    cause_mock.reason = cause_type

    await resource_handler(
        lifecycle=kopf.lifecycles.asap,
        registry=registry,
        resource=resource,
        event={'type': 'irrelevant', 'object': cause_mock.body},
        freeze=asyncio.Event(),
        replenished=asyncio.Event(),
        event_queue=asyncio.Queue(),
    )

    assert handlers.create_mock.call_count == (1 if cause_type == Reason.CREATE else 0)
    assert handlers.update_mock.call_count == (1 if cause_type == Reason.UPDATE else 0)
    assert handlers.delete_mock.call_count == (1 if cause_type == Reason.DELETE else 0)
    assert handlers.resume_mock.call_count == (1 if cause_type == Reason.RESUME else 0)

    assert not k8s_mocked.sleep_or_wait.called
    assert k8s_mocked.patch_obj.called

    patch = k8s_mocked.patch_obj.call_args_list[0][1]['patch']
    assert patch['status']['kopf']['progress'] is not None

    assert patch['status']['kopf']['progress'][name1]['retries'] == 1
    assert patch['status']['kopf']['progress'][name1]['success'] is True

    assert 'retries' not in patch['status']['kopf']['progress'][name2]
    assert 'success' not in patch['status']['kopf']['progress'][name2]

    assert 'started' in patch['status']['kopf']['progress'][name1]
    assert 'started' in patch['status']['kopf']['progress'][name2]


@pytest.mark.parametrize('cause_type', HANDLER_REASONS)
async def test_2nd_step_finishes_the_handlers(
        registry, handlers, extrahandlers,
        resource, cause_mock, cause_type, k8s_mocked):
    name1 = f'{cause_type}_fn'
    name2 = f'{cause_type}_fn2'

    cause_mock.reason = cause_type
    cause_mock.body.update({
        'status': {'kopf': {'progress': {
            name1: {'started': '1979-01-01T00:00:00', 'success': True},
            name2: {'started': '1979-01-01T00:00:00'},
        }}}
    })

    await resource_handler(
        lifecycle=kopf.lifecycles.one_by_one,
        registry=registry,
        resource=resource,
        event={'type': 'irrelevant', 'object': cause_mock.body},
        freeze=asyncio.Event(),
        replenished=asyncio.Event(),
        event_queue=asyncio.Queue(),
    )

    assert extrahandlers.create_mock.call_count == (1 if cause_type == Reason.CREATE else 0)
    assert extrahandlers.update_mock.call_count == (1 if cause_type == Reason.UPDATE else 0)
    assert extrahandlers.delete_mock.call_count == (1 if cause_type == Reason.DELETE else 0)
    assert extrahandlers.resume_mock.call_count == (1 if cause_type == Reason.RESUME else 0)

    assert not k8s_mocked.sleep_or_wait.called
    assert k8s_mocked.patch_obj.called

    patch = k8s_mocked.patch_obj.call_args_list[0][1]['patch']
    assert patch['status']['kopf']['progress'] is None
