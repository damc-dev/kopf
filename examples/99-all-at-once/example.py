"""
Kubernetes operator example: all the features at once (for debugging & testing).
"""
import pprint
import time

import kopf
import pykube
import yaml

# Marks for the e2e tests (see tests/e2e/test_examples.py):
E2E_CREATE_TIME = 5
E2E_DELETE_TIME = 1
E2E_SUCCESS_COUNTS = {'create_1': 1, 'create_2': 1, 'create_pod': 1, 'delete': 1}
E2E_FAILURE_COUNTS = {}
E2E_TRACEBACKS = True

try:
    cfg = pykube.KubeConfig.from_service_account()
except FileNotFoundError:
    cfg = pykube.KubeConfig.from_file()
api = pykube.HTTPClient(cfg)


@kopf.on.create('zalando.org', 'v1', 'kopfexamples')
def create_1(body, meta, spec, status, **kwargs):
    children = _create_children(owner=body)

    kopf.info(body, reason='AnyReason')
    kopf.event(body, type='Warning', reason='SomeReason', message="Cannot do something")
    kopf.event(children, type='Normal', reason='SomeReason', message="Created as part of the job1step")

    return {'job1-status': 100}


@kopf.on.create('zalando.org', 'v1', 'kopfexamples')
def create_2(body, meta, spec, status, retry=None, **kwargs):
    wait_for_something()  # specific for job2, e.g. an external API poller

    if not retry:
        # will be retried by the framework, even if it has been restarted
        raise Exception("Whoops!")

    return {'job2-status': 100}


@kopf.on.update('zalando.org', 'v1', 'kopfexamples')
def update(body, meta, spec, status, old, new, diff, **kwargs):
    print('Handling the diff')
    pprint.pprint(list(diff))


@kopf.on.field('zalando.org', 'v1', 'kopfexamples', field='spec.lst')
def update_lst(body, meta, spec, status, old, new, **kwargs):
    print(f'Handling the FIELD = {old} -> {new}')


@kopf.on.delete('zalando.org', 'v1', 'kopfexamples')
def delete(body, meta, spec, status, **kwargs):
    pass


def _create_children(owner):
    return []


def wait_for_something():
    # Note: intentionally blocking from the asyncio point of view.
    time.sleep(1)


@kopf.on.create('zalando.org', 'v1', 'kopfexamples')
def create_pod(**kwargs):

    # Render the pod yaml with some spec fields used in the template.
    pod_data = yaml.safe_load(f"""
        apiVersion: v1
        kind: Pod
        spec:
          containers:
          - name: the-only-one
            image: busybox
            command: ["sh", "-x", "-c", "sleep 1"]
    """)

    # Make it our child: assign the namespace, name, labels, owner references, etc.
    kopf.adopt(pod_data)
    kopf.label(pod_data, {'application': 'kopf-example-10'})

    # Actually create an object by requesting the Kubernetes API.
    pod = pykube.Pod(api, pod_data)
    pod.create()


@kopf.on.event('', 'v1', 'pods', labels={'application': 'kopf-example-10'})
def example_pod_change(logger, **kwargs):
    logger.info("This pod is special for us.")
