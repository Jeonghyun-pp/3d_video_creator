"""A fake RunPod REST API for the remote render rehearsal: the request and response shapes of
https://docs.runpod.io/api-reference/pods (POST/GET/DELETE /v1/pods), backed by local Docker containers built from
tests/remote/Dockerfile. Unknown body fields and missing auth are refused like the real API. It also serves a cached
Blender tarball (GET /blender/<file>) so a rehearsal downloads it from here instead of blender.org every time.
The user's key and a pod-scoped key are both accepted; a pod may delete only itself (as RunPod's pod key is assumed to).
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IMAGE = 'studio-fake-pod:1'
FIELDS = {'gpuTypeIds', 'gpuTypePriority', 'gpuCount', 'allowedCudaVersions', 'minRAMPerGPU', 'minVCPUPerGPU', 'cpuFlavorIds',
          'cpuFlavorPriority', 'vcpuCount', 'imageName', 'containerDiskInGb', 'volumeInGb', 'volumeMountPath', 'containerRegistryAuthId',
          'ports', 'globalNetworking', 'supportPublicIp', 'cloudType', 'dataCenterIds', 'dataCenterPriority', 'countryCodes', 'computeType',
          'name', 'env', 'dockerStartCmd', 'dockerEntrypoint', 'interruptible', 'locked', 'minDownloadMbps', 'minUploadMbps',
          'minDiskBandwidthMBps', 'networkVolumeId', 'templateId'}


class _Log(list):
    """The request log, also written to a file so a failed rehearsal still says who deleted what."""
    def __init__(self, path):
        super().__init__()
        self.path = Path(path) if path else None

    def append(self, row):
        super().append(row)
        if self.path:
            with self.path.open('a') as out:
                out.write(json.dumps([time.strftime('%H:%M:%S'), *row], default=str) + '\n')


class Fake:
    def __init__(self, user_key, blender_cache, cost_per_hr=0.99, log_file=None):
        self.user_key, self.cache, self.cost = user_key, Path(blender_cache), cost_per_hr
        self.pods, self.log, self.broken_next = {}, _Log(log_file), False
        self.server = ThreadingHTTPServer(('0.0.0.0', 0), self._handler())
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/v1'

    @property
    def url_in_pod(self):
        return f'http://host.docker.internal:{self.port}/v1'

    def close(self):
        for pod_id in list(self.pods):
            self._remove(pod_id)
        self.server.shutdown()

    def _remove(self, pod_id):
        pod = self.pods.pop(pod_id, None)
        if pod:
            subprocess.run(['docker', 'rm', '-f', pod['container']], capture_output=True)
        return pod

    def _create(self, body):
        pod_id = 'fake' + uuid.uuid4().hex[:10]
        pod_key = 'podkey_' + uuid.uuid4().hex
        env = {**(body.get('env') or {}), 'RUNPOD_POD_ID': pod_id, 'RUNPOD_API_KEY': pod_key}
        command = ['sleep', 'infinity'] if self.broken_next else list(body.get('dockerStartCmd') or ['bash', '-c', '/start.sh; sleep infinity'])
        self.broken_next = False
        # no --platform flag: the image is x86-64 already, and the flag makes Docker look it up in a registry
        args = ['docker', 'run', '-d', '--memory', '4g', '--cpus', '4', '-p', '0:22',
                '--add-host', 'host.docker.internal:host-gateway', '--label', 'studio-fake-pod=1']
        for k, v in env.items():
            args += ['-e', f'{k}={v}']
        container = subprocess.run([*args, IMAGE, *command], capture_output=True, text=True, check=True).stdout.strip()
        port = ''
        for _ in range(60):   # under x86 emulation the container takes a moment before Docker maps its port
            out = subprocess.run(['docker', 'port', container, '22/tcp'], capture_output=True, text=True)
            if out.returncode == 0 and out.stdout.strip():
                port = out.stdout.splitlines()[0].split(':')[-1].strip()
                break
            time.sleep(0.5)
        if not port:
            state = subprocess.run(['docker', 'inspect', '-f', '{{.State.Status}} {{.State.ExitCode}}', container], capture_output=True, text=True).stdout
            logs = subprocess.run(['docker', 'logs', container], capture_output=True, text=True).stderr[-300:]
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True)   # a server that failed to start is not left behind
            raise RuntimeError(f'container {container[:12]} has no port 22: {state.strip()} {logs}')
        pod = {'id': pod_id, 'name': body.get('name', 'my pod'), 'desiredStatus': 'RUNNING', 'publicIp': '127.0.0.1', 'portMappings': {'22': int(port)},
               'costPerHr': self.cost, 'gpu': {'displayName': 'RTX 5090 (fake)', 'count': 1}, 'imageName': body.get('imageName'),
               'container': container, 'pod_key': pod_key}
        self.pods[pod_id] = pod
        return pod

    def public(self, pod):
        return {k: v for k, v in pod.items() if k not in ('container', 'pod_key')}

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body=None):
                raw = json.dumps(body).encode() if body is not None else b''
                self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)

            def _who(self):
                token = (self.headers.get('Authorization') or '').removeprefix('Bearer ').strip()
                if token == fake.user_key:
                    return 'user'
                return next((p['id'] for p in fake.pods.values() if p['pod_key'] == token), None)

            def do_GET(self):
                if self.path.startswith('/blender/'):
                    file = fake.cache / Path(self.path).name
                    if not file.is_file():
                        return self._send(404, {'error': 'not cached'})
                    data = file.read_bytes()
                    self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
                    return None
                who = self._who()
                if who is None:
                    return self._send(401, {'error': 'Unauthorized'})
                fake.log.append(('GET', self.path, who))
                if self.path == '/v1/pods':
                    return self._send(200, [fake.public(p) for p in fake.pods.values()])
                pod = fake.pods.get(self.path.rsplit('/', 1)[-1])
                return self._send(200, fake.public(pod)) if pod else self._send(404, {'error': 'pod not found'})

            def do_POST(self):
                who = self._who()
                if who != 'user':
                    return self._send(401, {'error': 'Unauthorized'})
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                unknown = sorted(set(body) - FIELDS)
                if self.path != '/v1/pods' or unknown:
                    return self._send(400, {'error': f'unknown fields {unknown}'})
                fake.log.append(('POST', self.path, who, body.get('gpuTypeIds')))
                try:
                    return self._send(201, fake.public(fake._create(body)))
                except Exception as error:   # like a provider-side failure: a 500 with a reason, not a dropped connection
                    return self._send(500, {'error': f'could not start the server: {str(error)[-300:]}'})

            def do_DELETE(self):
                who = self._who()
                pod_id = self.path.rsplit('/', 1)[-1]
                if who is None or (who != 'user' and who != pod_id):   # a pod key reaches only its own pod
                    return self._send(401, {'error': 'Unauthorized'})
                fake.log.append(('DELETE', self.path, who))
                return self._send(200 if fake._remove(pod_id) else 404, None if pod_id else {'error': 'pod not found'})
        return Handler
