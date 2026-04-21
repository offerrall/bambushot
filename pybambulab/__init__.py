import io
import os
import re
import ssl
import json
import time
import zipfile
import subprocess
import xml.etree.ElementTree as ET

import paho.mqtt.client as mqtt

__all__ = ['BambuPrinter']


def _resolve_plate(file_path: str) -> int:
    with zipfile.ZipFile(file_path) as z:
        with z.open("Metadata/slice_info.config") as f:
            plate = ET.parse(f).getroot().find("plate")
    el = plate.find("metadata[@key='index']")
    if el is None:
        raise RuntimeError(f"{file_path}: no plate index in slice_info.config")
    return int(el.get("value"))


class BambuPrinter:
    _LINE_RE = re.compile(
        r'^([d-])[rwx-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\w+\s+\d+\s+[\d:]+\s+(.+)$'
    )

    def __init__(self, ip: str, access_code: str, serial: str) -> None:
        self._ip = ip
        self._access_code = access_code
        self._serial = serial

    def _curl(self, args: list[str], timeout: int = 30) -> str:
        cmd = [
            'curl', '--ftp-pasv', '--insecure', '--ssl-reqd',
            '--user', f'bblp:{self._access_code}',
            '--connect-timeout', '10',
            '--max-time', str(timeout),
            '--silent', '--show-error',
            *args,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        except FileNotFoundError:
            raise RuntimeError("curl not installed")
        except subprocess.TimeoutExpired:
            raise RuntimeError("curl timeout")
        if r.returncode != 0:
            raise RuntimeError(f"curl error {r.returncode}: {(r.stderr or '').strip()}")
        return r.stdout

    def list_files(self, path: str = '/') -> list[tuple[str, int, bool]]:
        if not path.startswith('/'):
            path = '/' + path
        if not path.endswith('/'):
            path += '/'
        output = self._curl([f'ftps://{self._ip}:990{path}'])
        files = []
        for line in output.splitlines():
            m = self._LINE_RE.match(line.strip())
            if not m:
                continue
            type_char, size_str, name = m.groups()
            if name in ('.', '..'):
                continue
            files.append((name, int(size_str), type_char == 'd'))
        return files

    def upload_gcode(self, local_file: str, timeout: int = 300) -> None:
        if not os.path.isfile(local_file):
            raise RuntimeError(f"file not found: {local_file}")
        remote_name = os.path.basename(local_file)
        self._curl(['-T', local_file, f'ftps://{self._ip}:990/{remote_name}'], timeout)

    def delete_file(self, name: str) -> None:
        self._curl([f'ftps://{self._ip}:990/', '-Q', f'DELE {name}'])

    def _publish(self, payload: dict) -> None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        published = False

        def on_publish(client, userdata, mid, *args):
            nonlocal published
            published = True

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set('bblp', self._access_code)
        client.tls_set_context(ctx)
        client.tls_insecure_set(True)
        client.on_publish = on_publish
        client.connect(self._ip, 8883, keepalive=10)
        client.loop_start()
        client.publish(f'device/{self._serial}/request', json.dumps(payload), qos=1)

        deadline = time.monotonic() + 5
        while not published and time.monotonic() < deadline:
            time.sleep(0.05)
        client.loop_stop()
        client.disconnect()

        if not published:
            raise RuntimeError("MQTT publish timed out")

    def print_file(self, local_file: str, use_ams: bool = False) -> None:
        plate = _resolve_plate(local_file)
        name = os.path.basename(local_file)
        self._publish({
            "print": {
                "sequence_id": "0",
                "command": "project_file",
                "param": f"Metadata/plate_{plate}.gcode",
                "subtask_name": name,
                "url": f"ftp:///{name}",
                "timelapse": False,
                "bed_leveling": True,
                "flow_cali": True,
                "vibration_cali": True,
                "layer_inspect": True,
                "ams_mapping": [0],
                "use_ams": use_ams,
                "project_id": "0",
                "profile_id": "0",
                "task_id": "0",
                "subtask_id": "0",
                "file": "",
                "bed_type": "auto",
            }
        })

    def send_and_print(self, local_file: str, use_ams: bool = False) -> None:
        name = os.path.basename(local_file)
        cached = {f[0] for f in self.list_files()}
        if name not in cached:
            self.upload_gcode(local_file)
        self.print_file(local_file, use_ams=use_ams)