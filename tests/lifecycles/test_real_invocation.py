import logging

import pytest

import kopf
from kopf.reactor.causation import ResourceChangingCause, Reason
from kopf.reactor.invocation import invoke
from kopf.structs.bodies import Body
from kopf.structs.patches import Patch


@pytest.mark.parametrize('lifecycle', [
    kopf.lifecycles.all_at_once,
    kopf.lifecycles.one_by_one,
    kopf.lifecycles.randomized,
    kopf.lifecycles.shuffled,
    kopf.lifecycles.asap,
])
async def test_protocol_invocation(lifecycle, resource):
    """
    To be sure that all kwargs are accepted properly.
    Especially when the new kwargs are added or an invocation protocol changed.
    """
    # The values are irrelevant, they can be anything.
    cause = ResourceChangingCause(
        logger=logging.getLogger('kopf.test.fake.logger'),
        resource=resource,
        patch=Patch(),
        body=Body(),
        initial=False,
        reason=Reason.NOOP,
    )
    handlers = []
    selected = await invoke(lifecycle, handlers, cause=cause)
    assert isinstance(selected, (tuple, list))
    assert len(selected) == 0
