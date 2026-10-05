"""Check this host's Docker CPU binding and shared leases; no models or candidates."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aad.io import save_json
from aad.v5_1_cpu import CorePool, detect_policy, PinnedContainers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New diagnostic output directory')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    import docker

    @contextmanager
    def client_factory():
        client = docker.from_env()
        try:
            yield client
        finally:
            client.close()

    image = 'rust:1.79.0-buster'
    with client_factory() as client:
        policy = detect_policy(client, image)
    save_json(args.output / 'policy.json', policy)
    first = CorePool(client_factory, image, policy, args.output / 'leases')
    second = CorePool(client_factory, image, policy, args.output / 'leases')
    acquired = threading.Event()
    starting = threading.Event()

    def waiting_probe():
        starting.set()
        with second.lease() as lease:
            acquired.set()
            local = threading.local()
            local.lease = lease
            with client_factory() as client:
                output = PinnedContainers(client.containers, local, policy).run(
                    image, ['sh', '-c', 'grep Cpus_allowed_list /proc/self/status'],
                    entrypoint=[], remove=True, network_disabled=True, mem_limit='32m')
            allowed = output.decode().strip().split()[-1]
            if allowed != str(lease['cpu']):
                raise RuntimeError('Actual CPU affinity differs from reservation')
            return {'cpu': lease['cpu'], 'siblings': lease['siblings'], 'actual_affinity': allowed,
                    'wait_seconds': lease['wait_seconds']}

    with ThreadPoolExecutor(max_workers=1) as executor:
        reservations = ExitStack()
        try:
            for _ in range(first.capacity):
                reservations.enter_context(first.lease())
            print(f'Reserved all {first.capacity} evaluation core groups.', flush=True)
            future = executor.submit(waiting_probe)
            starting.wait()
            if acquired.wait(1):
                raise RuntimeError('A busy core was incorrectly reused')
            print('Extra evaluation correctly waits for a free core.', flush=True)
        finally:
            reservations.close()
        result = future.result(timeout=60)
    report = {'capacity': first.capacity, 'service_cpus': policy['service_cpus'],
              'blocked_when_full': True, 'resumed_after_release': True, **result}
    save_json(args.output / 'report.json', report)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
