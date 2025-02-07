"""
Conversion of low-level events to high-level causes, and handling them.

These functions are invoked from the queueing module `kopf.reactor.queueing`,
which are the actual event loop of the operator process.

The conversion of the low-level events to the high-level causes is done by
checking the object's state and comparing it to the preserved last-seen state.

The framework itself makes the necessary changes to the object, -- such as the
finalizers attachment, last-seen state updates, and handler status tracking, --
thus provoking the low-level watch-events and additional queueing calls.
But these internal changes are filtered out from the cause detection
and therefore do not trigger the user-defined handlers.
"""

import asyncio
import collections.abc
import datetime
from contextvars import ContextVar
from typing import Optional, Iterable, Collection, Any

from kopf.clients import patching
from kopf.engines import logging as logging_engine
from kopf.engines import posting
from kopf.engines import sleeping
from kopf.reactor import causation
from kopf.reactor import invocation
from kopf.reactor import lifecycles
from kopf.reactor import registries
from kopf.reactor import state
from kopf.structs import bodies
from kopf.structs import dicts
from kopf.structs import diffs
from kopf.structs import finalizers
from kopf.structs import lastseen
from kopf.structs import patches
from kopf.structs import resources

WAITING_KEEPALIVE_INTERVAL = 10 * 60
""" How often to wake up from the long sleep, to show the liveliness. """

DEFAULT_RETRY_DELAY = 1 * 60
""" The default delay duration for the regular exception in retry-mode. """


class PermanentError(Exception):
    """ A fatal handler error, the retries are useless. """


class TemporaryError(Exception):
    """ A potentially recoverable error, should be retried. """
    def __init__(
            self,
            __msg: Optional[str] = None,
            delay: Optional[float] = DEFAULT_RETRY_DELAY,
    ):
        super().__init__(__msg)
        self.delay = delay


class HandlerTimeoutError(PermanentError):
    """ An error for the handler's timeout (if set). """


class HandlerChildrenRetry(TemporaryError):
    """ An internal pseudo-error to retry for the next sub-handlers attempt. """


# The task-local context; propagated down the stack instead of multiple kwargs.
# Used in `@kopf.on.this` and `kopf.execute()` to add/get the sub-handlers.
sublifecycle_var: ContextVar[lifecycles.LifeCycleFn] = ContextVar('sublifecycle_var')
subregistry_var: ContextVar[registries.ResourceRegistry] = ContextVar('subregistry_var')
subexecuted_var: ContextVar[bool] = ContextVar('subexecuted_var')
handler_var: ContextVar[registries.ResourceHandler] = ContextVar('handler_var')
cause_var: ContextVar[causation.BaseCause] = ContextVar('cause_var')


async def resource_handler(
        lifecycle: lifecycles.LifeCycleFn,
        registry: registries.OperatorRegistry,
        resource: resources.Resource,
        event: bodies.Event,
        freeze: asyncio.Event,
        replenished: asyncio.Event,
        event_queue: posting.K8sEventQueue,
) -> None:
    """
    Handle a single custom object low-level watch-event.

    Convert the low-level events, as provided by the watching/queueing tasks,
    to the high-level causes, and then call the cause-handling logic.

    All the internally provoked changes are intercepted, do not create causes,
    and therefore do not call the handling logic.
    """
    body: bodies.Body = event['object']
    patch: patches.Patch = patches.Patch()
    delay: Optional[float] = None

    # Each object has its own prefixed logger, to distinguish parallel handling.
    logger = logging_engine.ObjectLogger(body=body)
    posting.event_queue_loop_var.set(asyncio.get_running_loop())
    posting.event_queue_var.set(event_queue)  # till the end of this object's task.

    # If the global freeze is set for the processing (i.e. other operator overrides), do nothing.
    if freeze.is_set():
        logger.debug("Ignoring the events due to freeze.")
        return

    # Invoke all silent spies. No causation, no progress storage is performed.
    if registry.has_resource_watching_handlers(resource=resource):
        resource_watching_cause = causation.detect_resource_watching_cause(
            event=event,
            resource=resource,
            logger=logger,
            patch=patch,
        )
        await handle_resource_watching_cause(
            lifecycle=lifecycles.all_at_once,
            registry=registry,
            cause=resource_watching_cause,
        )

    # Object patch accumulator. Populated by the methods. Applied in the end of the handler.
    # Detect the cause and handle it (or at least log this happened).
    if registry.has_resource_changing_handlers(resource=resource):
        extra_fields = registry.get_extra_fields(resource=resource)
        old, new, diff = lastseen.get_essential_diffs(body=body, extra_fields=extra_fields)
        resource_changing_cause = causation.detect_resource_changing_cause(
            event=event,
            resource=resource,
            logger=logger,
            patch=patch,
            old=old,
            new=new,
            diff=diff,
            requires_finalizer=registry.requires_finalizer(resource=resource, body=body),
        )
        delay = await handle_resource_changing_cause(
            lifecycle=lifecycle,
            registry=registry,
            cause=resource_changing_cause,
        )

    # Whatever was done, apply the accumulated changes to the object.
    # But only once, to reduce the number of API calls and the generated irrelevant events.
    if patch:
        logger.debug("Patching with: %r", patch)
        await patching.patch_obj(resource=resource, patch=patch, body=body)

    # Sleep strictly after patching, never before -- to keep the status proper.
    # The patching above, if done, interrupts the sleep instantly, so we skip it at all.
    if delay and not patch:
        logger.debug(f"Sleeping for {delay} seconds for the delayed handlers.")
        unslept = await sleeping.sleep_or_wait(delay, replenished)
        if unslept is not None:
            logger.debug(f"Sleeping was interrupted by new changes, {unslept} seconds left.")
        else:
            now = datetime.datetime.utcnow()
            dummy = patches.Patch({'status': {'kopf': {'dummy': now.isoformat()}}})
            logger.debug("Provoking reaction with: %r", dummy)
            await patching.patch_obj(resource=resource, patch=dummy, body=body)


async def handle_resource_watching_cause(
        lifecycle: lifecycles.LifeCycleFn,
        registry: registries.OperatorRegistry,
        cause: causation.ResourceWatchingCause,
) -> None:
    """
    Handle a received event, log but ignore all errors.

    This is a lightweight version of the cause handling, but for the raw events,
    without any progress persistence. Multi-step calls are also not supported.
    If the handler fails, it fails and is never retried.

    Note: K8s-event posting is skipped for `kopf.on.event` handlers,
    as they should be silent. Still, the messages are logged normally.
    """
    logger = cause.logger
    handlers = registry.get_resource_watching_handlers(cause=cause)
    for handler in handlers:

        # The exceptions are handled locally and are not re-raised, to keep the operator running.
        try:
            logger.debug(f"Invoking handler {handler.id!r}.")

            result = await _call_handler(
                handler,
                cause=cause,
                lifecycle=lifecycle,
            )

        except Exception:
            logger.exception(f"Handler {handler.id!r} failed with an exception. Will ignore.", local=True)

        else:
            logger.info(f"Handler {handler.id!r} succeeded.", local=True)
            state.store_result(patch=cause.patch, handler=handler, result=result)


async def handle_resource_changing_cause(
        lifecycle: lifecycles.LifeCycleFn,
        registry: registries.OperatorRegistry,
        cause: causation.ResourceChangingCause,
) -> Optional[float]:
    """
    Handle a detected cause, as part of the bigger handler routine.
    """
    logger = cause.logger
    patch = cause.patch  # TODO get rid of this alias
    body = cause.body  # TODO get rid of this alias
    delay = None
    done = None
    skip = None

    # Regular causes invoke the handlers.
    if cause.reason in causation.HANDLER_REASONS:
        title = causation.TITLES.get(cause.reason, repr(cause.reason))
        logger.debug(f"{title.capitalize()} event: %r", body)
        if cause.diff is not None and cause.old is not None and cause.new is not None:
            logger.debug(f"{title.capitalize()} diff: %r", cause.diff)

        handlers = registry.get_resource_changing_handlers(cause=cause)
        if handlers:
            try:
                await _execute(
                    lifecycle=lifecycle,
                    handlers=handlers,
                    cause=cause,
                )
            except HandlerChildrenRetry as e:
                # on the top-level, no patches -- it is pre-patched.
                delay = e.delay
                done = False
            else:
                logger.info(f"All handlers succeeded for {title}.")
                done = True
        else:
            skip = True

    # Regular causes also do some implicit post-handling when all handlers are done.
    if done or skip:
        extra_fields = registry.get_extra_fields(resource=cause.resource)
        lastseen.refresh_essence(body=body, patch=patch, extra_fields=extra_fields)
        if done:
            state.purge_progress(body=body, patch=patch)
        if cause.reason == causation.Reason.DELETE:
            logger.debug("Removing the finalizer, thus allowing the actual deletion.")
            finalizers.remove_finalizers(body=body, patch=patch)

    # Informational causes just print the log lines.
    if cause.reason == causation.Reason.GONE:
        logger.debug("Deleted, really deleted, and we are notified.")

    if cause.reason == causation.Reason.FREE:
        logger.debug("Deletion event, but we are done with it, and we do not care.")

    if cause.reason == causation.Reason.NOOP:
        logger.debug("Something has changed, but we are not interested (state is the same).")

    # For the case of a newly created object, or one that doesn't have the correct
    # finalizers, lock it to this operator. Not all newly created objects will
    # produce an 'ACQUIRE' causation event. This only happens when there are
    # mandatory deletion handlers registered for the given object, i.e. if finalizers
    # are required.
    if cause.reason == causation.Reason.ACQUIRE:
        logger.debug("Adding the finalizer, thus preventing the actual deletion.")
        finalizers.append_finalizers(body=body, patch=patch)

    # Remove finalizers from an object, since the object currently has finalizers, but
    # shouldn't, thus releasing the locking of the object to this operator.
    if cause.reason == causation.Reason.RELEASE:
        logger.debug("Removing the finalizer, as there are no handlers requiring it.")
        finalizers.remove_finalizers(body=body, patch=patch)

    # The delay is then consumed by the main handling routine (in different ways).
    return delay


async def execute(
        *,
        fns: Optional[Iterable[invocation.Invokable]] = None,
        handlers: Optional[Iterable[registries.ResourceHandler]] = None,
        registry: Optional[registries.ResourceRegistry] = None,
        lifecycle: Optional[lifecycles.LifeCycleFn] = None,
        cause: Optional[causation.BaseCause] = None,
) -> None:
    """
    Execute the handlers in an isolated lifecycle.

    This function is just a public wrapper for `execute` with multiple
    ways to specify the handlers: either as the raw functions, or as the
    pre-created handlers, or as a registry (as used in the object handling).

    If no explicit functions or handlers or registry are passed,
    the sub-handlers of the current handler are assumed, as accumulated
    in the per-handler registry with ``@kopf.on.this``.

    If the call to this method for the sub-handlers is not done explicitly
    in the handler, it is done implicitly after the handler is exited.
    One way or another, it is executed for the sub-handlers.
    """

    # Restore the current context as set in the handler execution cycle.
    lifecycle = lifecycle if lifecycle is not None else sublifecycle_var.get()
    cause = cause if cause is not None else cause_var.get()
    handler: registries.ResourceHandler = handler_var.get()

    # Validate the inputs; the function signatures cannot put these kind of restrictions, so we do.
    if len([v for v in [fns, handlers, registry] if v is not None]) > 1:
        raise TypeError("Only one of the fns, handlers, registry can be passed. Got more.")

    elif fns is not None and isinstance(fns, collections.abc.Mapping):
        registry = registries.ResourceRegistry(prefix=handler.id if handler else None)
        for id, fn in fns.items():
            registry.register(fn=fn, id=id)

    elif fns is not None and isinstance(fns, collections.abc.Iterable):
        registry = registries.ResourceRegistry(prefix=handler.id if handler else None)
        for fn in fns:
            registry.register(fn=fn)

    elif fns is not None:
        raise ValueError(f"fns must be a mapping or an iterable, got {fns.__class__}.")

    elif handlers is not None:
        registry = registries.ResourceRegistry(prefix=handler.id if handler else None)
        for handler in handlers:
            registry.append(handler=handler)

    # Use the registry as is; assume that the caller knows what they do.
    elif registry is not None:
        pass

    # Prevent double implicit execution.
    elif subexecuted_var.get():
        return

    # If no explicit args were passed, implicitly use the accumulated handlers from `@kopf.on.this`.
    else:
        subexecuted_var.set(True)
        registry = subregistry_var.get()

    # The sub-handlers are only for upper-level causes, not for lower-level events.
    if not isinstance(cause, causation.ResourceChangingCause):
        raise RuntimeError("Sub-handlers of event-handlers are not supported and have "
                           "no practical use (there are no retries or state tracking).")

    # Execute the real handlers (all or few or one of them, as per the lifecycle).
    # Raises `HandlerChildrenRetry` if the execute should be continued on the next iteration.
    await _execute(
        lifecycle=lifecycle,
        handlers=registry.get_resource_changing_handlers(cause=cause),
        cause=cause,
    )


async def _execute(
        lifecycle: lifecycles.LifeCycleFn,
        handlers: Collection[registries.ResourceHandler],
        cause: causation.BaseCause,
        retry_on_errors: bool = True,
) -> None:
    """
    Call the next handler(s) from the chain of the handlers.

    Keep the record on the progression of the handlers in the object's status,
    and use it on the next invocation to determined which handler(s) to call.

    This routine is used both for the global handlers (via global registry),
    and for the sub-handlers (via a simple registry of the current handler).

    Raises `HandlerChildrenRetry` if there are children handlers to be executed
    on the next call, and implicitly provokes such a call by making the changes
    to the status fields (on the handler progression and number of retries).

    Exits normally if all handlers for this cause are fully done.
    """
    logger = cause.logger

    # Filter and select the handlers to be executed right now, on this event reaction cycle.
    handlers_done = [h for h in handlers if state.is_finished(body=cause.body, handler=h)]
    handlers_wait = [h for h in handlers if state.is_sleeping(body=cause.body, handler=h)]
    handlers_todo = [h for h in handlers if state.is_awakened(body=cause.body, handler=h)]
    handlers_plan = [h for h in await invocation.invoke(lifecycle, handlers_todo, cause=cause)]
    handlers_left = [h for h in handlers_todo if h.id not in {h.id for h in handlers_plan}]

    # Set the timestamps -- even if not executed on this event, but just got registered.
    for handler in handlers:
        if not state.is_started(body=cause.body, handler=handler):
            state.set_start_time(body=cause.body, patch=cause.patch, handler=handler)

    # Execute all planned (selected) handlers in one event reaction cycle, even if there are few.
    for handler in handlers_plan:

        # Restore the handler's progress status. It can be useful in the handlers.
        retry = state.get_retry_count(body=cause.body, handler=handler)
        started = state.get_start_time(body=cause.body, handler=handler, patch=cause.patch)
        runtime = datetime.datetime.utcnow() - (started if started else datetime.datetime.utcnow())

        # The exceptions are handled locally and are not re-raised, to keep the operator running.
        try:
            logger.debug(f"Invoking handler {handler.id!r}.")

            if handler.timeout is not None and runtime.total_seconds() > handler.timeout:
                raise HandlerTimeoutError(f"Handler {handler.id!r} has timed out after {runtime}.")

            result = await _call_handler(
                handler,
                cause=cause,
                retry=retry,
                started=started,
                runtime=runtime,
                lifecycle=lifecycle,  # just a default for the sub-handlers, not used directly.
            )

        # Unfinished children cause the regular retry, but with less logging and event reporting.
        except HandlerChildrenRetry as e:
            logger.debug(f"Handler {handler.id!r} has unfinished sub-handlers. Will retry soon.")
            state.set_retry_time(body=cause.body, patch=cause.patch, handler=handler, delay=e.delay)
            handlers_left.append(handler)

        # Definitely a temporary error, regardless of the error strictness.
        except TemporaryError as e:
            logger.error(f"Handler {handler.id!r} failed temporarily: %s", str(e) or repr(e))
            state.set_retry_time(body=cause.body, patch=cause.patch, handler=handler, delay=e.delay)
            handlers_left.append(handler)

        # Same as permanent errors below, but with better logging for our internal cases.
        except HandlerTimeoutError as e:
            logger.error(f"%s", str(e) or repr(e))  # already formatted
            state.store_failure(body=cause.body, patch=cause.patch, handler=handler, exc=e)
            # TODO: report the handling failure somehow (beside logs/events). persistent status?

        # Definitely a permanent error, regardless of the error strictness.
        except PermanentError as e:
            logger.error(f"Handler {handler.id!r} failed permanently: %s", str(e) or repr(e))
            state.store_failure(body=cause.body, patch=cause.patch, handler=handler, exc=e)
            # TODO: report the handling failure somehow (beside logs/events). persistent status?

        # Regular errors behave as either temporary or permanent depending on the error strictness.
        except Exception as e:
            if retry_on_errors:
                logger.exception(f"Handler {handler.id!r} failed with an exception. Will retry.")
                state.set_retry_time(body=cause.body, patch=cause.patch, handler=handler, delay=DEFAULT_RETRY_DELAY)
                handlers_left.append(handler)
            else:
                logger.exception(f"Handler {handler.id!r} failed with an exception. Will stop.")
                state.store_failure(body=cause.body, patch=cause.patch, handler=handler, exc=e)
                # TODO: report the handling failure somehow (beside logs/events). persistent status?

        # No errors means the handler should be excluded from future runs in this reaction cycle.
        else:
            logger.info(f"Handler {handler.id!r} succeeded.")
            state.store_success(body=cause.body, patch=cause.patch, handler=handler, result=result)

    # Provoke the retry of the handling cycle if there were any unfinished handlers,
    # either because they were not selected by the lifecycle, or failed and need a retry.
    if handlers_left:
        raise HandlerChildrenRetry(delay=None)

    # If there are delayed handlers, block this object's cycle; but do keep-alives every few mins.
    # Other (non-delayed) handlers will continue as normlally, due to raise few lines above.
    # Other objects will continue as normally in their own handling asyncio tasks.
    if handlers_wait:
        now = datetime.datetime.utcnow()
        limit = now + datetime.timedelta(seconds=WAITING_KEEPALIVE_INTERVAL)
        times = [state.get_awake_time(body=cause.body, handler=h) for h in handlers_wait]
        until = min([t for t in times if t is not None] + [limit])  # the soonest awake datetime.
        delay = max(0, (until - now).total_seconds())
        raise HandlerChildrenRetry(delay=delay)


async def _call_handler(
        handler: registries.ResourceHandler,
        *args: Any,
        cause: causation.BaseCause,
        lifecycle: lifecycles.LifeCycleFn,
        **kwargs: Any,
) -> Any:
    """
    Invoke one handler only, according to the calling conventions.

    Specifically, calculate the handler-specific fields (e.g. field diffs).

    Ensure the global context for this asyncio task is set to the handler and
    its cause -- for proper population of the sub-handlers via the decorators
    (see `@kopf.on.this`).
    """

    # For the field-handlers, the old/new/diff values must match the field, not the whole object.
    if isinstance(cause, causation.ResourceChangingCause) and handler.field is not None:
        old = dicts.resolve(cause.old, handler.field, None, assume_empty=True)
        new = dicts.resolve(cause.new, handler.field, None, assume_empty=True)
        diff = diffs.reduce(cause.diff, handler.field)
        cause = causation.enrich_cause(cause=cause, old=old, new=new, diff=diff)

    # Store the context of the current resource-object-event-handler, to be used in `@kopf.on.this`,
    # and maybe other places, and consumed in the recursive `execute()` calls for the children.
    # This replaces the multiple kwargs passing through the whole call stack (easy to forget).
    with invocation.context([
        (sublifecycle_var, lifecycle),
        (subregistry_var, registries.ResourceRegistry(prefix=handler.id)),
        (subexecuted_var, False),
        (handler_var, handler),
        (cause_var, cause),
    ]):
        # And call it. If the sub-handlers are not called explicitly, run them implicitly
        # as if it was done inside of the handler (i.e. under try-finally block).
        result = await invocation.invoke(
            handler.fn,
            *args,
            cause=cause,
            **kwargs,
        )

        if not subexecuted_var.get() and isinstance(cause, causation.ResourceChangingCause):
            await execute()

        return result
