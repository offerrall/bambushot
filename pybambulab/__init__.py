import os
import re
import ssl
import json
import tempfile
import zipfile
import subprocess
import paho.mqtt.client as mqtt


class BambuPrinter:
    _LINE_RE = re.compile(
        r'^([d-])[rwx-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+'
        r'\w+\s+\d+\s+[\d:]+\s+(.+)$'
    )
    _PLATE_RE = re.compile(r'^Metadata/plate_(\d+)\.gcode$')
    _META_SUFFIX = '.plate'

    def __init__(self, ip: str, access_code: str, serial: str) -> None:
        self._ip = ip
        self._access_code = access_code
        self._serial = serial

    def _curl(self, args: list[str], timeout: int) -> str:
        cmd = [
            'curl', '--ftp-pasv', '--insecure', '--ssl-reqd',
            '--user', f'bblp:{self._access_code}',
            '--connect-timeout', '10',
            '--max-time', str(timeout),
            '--silent', '--show-error',
            *args,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout + 10)
        except FileNotFoundError:
            raise RuntimeError("curl not installed")
        except subprocess.TimeoutExpired:
            raise RuntimeError("curl timeout")
        if r.returncode != 0:
            raise RuntimeError(f"curl {r.returncode}: {(r.stderr or '').strip()}")
        return r.stdout

    def _resolve_plate(self, file_path: str) -> int:
        plates: list[int] = []
        with zipfile.ZipFile(file_path, 'r') as z:
            for info in z.infolist():
                m = self._PLATE_RE.match(info.filename)
                if m and info.file_size > 0:
                    plates.append(int(m.group(1)))
        plates = sorted(set(plates))
        if not plates:
            raise RuntimeError(f"{file_path} has no sliced plate")
        if len(plates) > 1:
            raise RuntimeError(f"{file_path} has multiple sliced plates: {plates}")
        return plates[0]

    def _read_meta(self, name: str, timeout: int = 30) -> int:
        try:
            content = self._curl(
                [f'ftps://{self._ip}:990/{name}{self._META_SUFFIX}'],
                timeout,
            ).strip()
            return int(content)
        except (RuntimeError, ValueError):
            raise RuntimeError(
                f"{name} has no plate metadata. "
                f"Upload it with upload_gcode() first."
            )

    def _delete_remote(self, name: str, timeout: int) -> None:
        self._curl(
            [f'ftps://{self._ip}:990/', '-Q', f'DELE {name}'],
            timeout,
        )

    def _publish(self, payload: dict) -> None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set('bblp', self._access_code)
        client.tls_set_context(ctx)
        client.tls_insecure_set(True)

        client.connect(self._ip, 8883, keepalive=30)
        client.loop_start()
        client.publish(
            f'device/{self._serial}/request', json.dumps(payload), qos=0
        )
        client.loop_stop()
        client.disconnect()

    def list_files(
        self, path: str = '/', timeout: int = 30
    ) -> list[tuple[str, int, bool, str]]:
        if not path.startswith('/'):
            path = '/' + path
        if not path.endswith('/'):
            path = path + '/'

        output = self._curl([f'ftps://{self._ip}:990{path}'], timeout)

        files: list[tuple[str, int, bool, str]] = []
        for line in output.splitlines():
            m = self._LINE_RE.match(line.strip())
            if not m:
                continue
            type_char, size_str, name = m.groups()
            if name in ('.', '..'):
                continue
            if name.endswith(self._META_SUFFIX):
                continue
            full = (path.rstrip('/') + '/' + name) if path != '/' else '/' + name
            files.append((name, int(size_str), type_char == 'd', full))
        return files

    def upload_gcode(self, local_file: str, timeout: int = 300) -> None:
        if not os.path.isfile(local_file):
            raise RuntimeError(f"file not found: {local_file}")

        plate = self._resolve_plate(local_file)
        remote_name = os.path.basename(local_file)

        self._curl(
            ['-T', local_file, f'ftps://{self._ip}:990/{remote_name}'],
            timeout,
        )

        with tempfile.NamedTemporaryFile(
            mode='w', delete=False, encoding='utf-8'
        ) as f:
            f.write(str(plate))
            meta_local = f.name
        try:
            self._curl(
                ['-T', meta_local,
                 f'ftps://{self._ip}:990/{remote_name}{self._META_SUFFIX}'],
                timeout,
            )
        finally:
            os.remove(meta_local)

    def delete_file(self, name: str, timeout: int = 30) -> None:
        self._delete_remote(name, timeout)
        try:
            self._delete_remote(name + self._META_SUFFIX, timeout)
        except RuntimeError:
            pass

    def print_file(self, name: str, use_ams: bool = False) -> None:
        plate = self._read_meta(name)

        payload = {
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
        }
        self._publish(payload)


__all__ = ['BambuPrinter']