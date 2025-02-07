import copy
import json

import pytest

from kopf.reactor.causation import Reason, detect_resource_changing_cause
from kopf.structs.finalizers import FINALIZER
from kopf.structs.lastseen import LAST_SEEN_ANNOTATION

# Encoded at runtime, so that we do not make any assumptions on json formatting.
SPEC_DATA = {'spec': {'field': 'value'}}
SPEC_JSON = json.dumps((SPEC_DATA))
ALT_DATA = {'spec': {'field': 'other'}}
ALT_JSON = json.dumps((ALT_DATA))

#
# The following factors contribute to the detection of the cause
# (and we combine all of them with the matching & mismatching fixtures):
# * Finalizers (presence or absence).
# * Deletion timestamp (presence or absence).
# * Annotation with the last-seen state (presence or absence).
# * Annotation with the last-seen state (difference with the real state).
#

deleted_events = pytest.mark.parametrize('event', [
    pytest.param('DELETED'),
])

regular_events = pytest.mark.parametrize('event', [
    pytest.param('ADDED'),
    pytest.param('MODIFIED'),
    pytest.param('FORWARD-COMPATIBILITY-PSEUDO-EVENT', id='COMPAT'),
])


all_finalizers = pytest.mark.parametrize('finalizers', [
    pytest.param({}, id='no-finalizers'),
    pytest.param({'finalizers': [FINALIZER]}, id='own-finalizer'),
    pytest.param({'finalizers': ['irrelevant', 'another']}, id='other-finalizers'),
    pytest.param({'finalizers': ['irrelevant', FINALIZER, 'another']}, id='mixed-finalizers'),
])

our_finalizers = pytest.mark.parametrize('finalizers', [
    pytest.param({'finalizers': [FINALIZER]}, id='own-finalizer'),
    pytest.param({'finalizers': ['irrelevant', FINALIZER, 'another']}, id='mixed-finalizers'),
])

no_finalizers = pytest.mark.parametrize('finalizers', [
    pytest.param({}, id='no-finalizers'),
    pytest.param({'finalizers': ['irrelevant', 'another']}, id='other-finalizers'),
])


all_deletions = pytest.mark.parametrize('deletion_ts', [
    pytest.param({}, id='no-deletion-ts'),
    pytest.param({'deletionTimestamp': None}, id='empty-deletion-ts'),
    pytest.param({'deletionTimestamp': 'some'}, id='real-deletion-ts'),
])

real_deletions = pytest.mark.parametrize('deletion_ts', [
    pytest.param({'deletionTimestamp': 'some'}, id='real-deletion-ts'),
])

no_deletions = pytest.mark.parametrize('deletion_ts', [
    pytest.param({}, id='no-deletion-ts'),
    pytest.param({'deletionTimestamp': None}, id='empty-deletion-ts'),
])


all_lastseen = pytest.mark.parametrize('annotations', [
    pytest.param({}, id='no-annotations'),
    pytest.param({'annotations': {}}, id='no-last-seen'),
    pytest.param({'annotations': {LAST_SEEN_ANNOTATION: SPEC_JSON}}, id='matching-last-seen'),
    pytest.param({'annotations': {LAST_SEEN_ANNOTATION: SPEC_JSON}}, id='mismatching-last-seen'),
])

absent_lastseen = pytest.mark.parametrize('annotations', [
    pytest.param({}, id='no-annotations'),
    pytest.param({'annotations': {}}, id='no-last-seen'),
])

matching_lastseen = pytest.mark.parametrize('annotations', [
    pytest.param({'annotations': {LAST_SEEN_ANNOTATION: SPEC_JSON}}, id='matching-last-seen'),
])

mismatching_lastseen = pytest.mark.parametrize('annotations', [
    pytest.param({'annotations': {LAST_SEEN_ANNOTATION: ALT_JSON}}, id='mismatching-last-seen'),
])

all_requires_finalizer = pytest.mark.parametrize('requires_finalizer', [
    pytest.param(True, id='requires-finalizer'),
    pytest.param(False, id='doesnt-require-finalizer'),
])

requires_finalizer = pytest.mark.parametrize('requires_finalizer', [
    pytest.param(True, id='requires-finalizer'),
])

doesnt_require_finalizer = pytest.mark.parametrize('requires_finalizer', [
    pytest.param(False, id='doesnt-require-finalizer'),
])


@pytest.fixture
def content():
    return copy.deepcopy(SPEC_DATA)


#
# kwargs helpers -- to test them for all causes.
#

@pytest.fixture()
def kwargs():
    return dict(
        resource=object(),
        logger=object(),
        patch=object(),
    )

def check_kwargs(cause, kwargs):
    __traceback_hide__ = True
    assert cause.resource is kwargs['resource']
    assert cause.logger is kwargs['logger']
    assert cause.patch is kwargs['patch']


#
# The tests.
#

@all_requires_finalizer
@all_finalizers
@all_deletions
@deleted_events
def test_for_gone(kwargs, event, finalizers, deletion_ts, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.GONE
    check_kwargs(cause, kwargs)


@all_requires_finalizer
@no_finalizers
@real_deletions
@regular_events
def test_for_free(kwargs, event, finalizers, deletion_ts, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.FREE
    check_kwargs(cause, kwargs)


@all_requires_finalizer
@our_finalizers
@real_deletions
@regular_events
def test_for_delete(kwargs, event, finalizers, deletion_ts, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.DELETE
    check_kwargs(cause, kwargs)


@requires_finalizer
@no_finalizers
@no_deletions
@regular_events
def test_for_acquire(kwargs, event, finalizers, deletion_ts, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.ACQUIRE
    check_kwargs(cause, kwargs)


@doesnt_require_finalizer
@our_finalizers
@no_deletions
@regular_events
def test_for_release(kwargs, event, finalizers, deletion_ts, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.RELEASE
    check_kwargs(cause, kwargs)


@requires_finalizer
@absent_lastseen
@our_finalizers
@no_deletions
@regular_events
def test_for_create(kwargs, event, finalizers, deletion_ts, annotations, content, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object'].update(content)
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    event['object']['metadata'].update(annotations)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.CREATE
    check_kwargs(cause, kwargs)


@doesnt_require_finalizer
@no_finalizers
@no_deletions
@regular_events
def test_for_create_skip_acquire(kwargs, event, finalizers, deletion_ts, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.CREATE
    check_kwargs(cause, kwargs)


@requires_finalizer
@matching_lastseen
@our_finalizers
@no_deletions
@regular_events
def test_for_no_op(kwargs, event, finalizers, deletion_ts, annotations, content, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object'].update(content)
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    event['object']['metadata'].update(annotations)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        **kwargs)
    assert cause.reason == Reason.NOOP
    check_kwargs(cause, kwargs)


@requires_finalizer
@mismatching_lastseen
@our_finalizers
@no_deletions
@regular_events
def test_for_update(kwargs, event, finalizers, deletion_ts, annotations, content, requires_finalizer):
    event = {'type': event, 'object': {'metadata': {}}}
    event['object'].update(content)
    event['object']['metadata'].update(finalizers)
    event['object']['metadata'].update(deletion_ts)
    event['object']['metadata'].update(annotations)
    cause = detect_resource_changing_cause(
        event=event,
        requires_finalizer=requires_finalizer,
        diff=True,
        **kwargs)
    assert cause.reason == Reason.UPDATE
    check_kwargs(cause, kwargs)
