"""Local-only integration: MosDNS -> proxy SOCKS -> local TCP DNS fixture."""
import json
import secrets
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.native import find_core, write_json


_assigned_ports = set()


def free_port():
    # UDP and TCP exclusions differ on Windows; reserve a port available to both.
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, socket.socket() as tcp:
            port = 10000 + secrets.randbelow(39000)
            if port in _assigned_ports:
                continue
            try:
                udp.bind(("127.0.0.1", port))
                tcp.bind(("127.0.0.1", port))
                _assigned_ports.add(port)
                return port
            except OSError:
                pass
    raise RuntimeError("no shared TCP/UDP fixture port")


def dns_reply(query):
    # A response to the exact incoming question; no public resolver is contacted.
    end = 12
    while query[end]:
        end += query[end] + 1
    end += 5  # terminating label plus QTYPE/QCLASS; exclude incoming EDNS OPT.
    return query[:2] + b'\x81\x80' + query[4:6] + b'\x00\x01\x00\x00\x00\x00' + query[12:end] + b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x7f\x00\x00\x2a'


class UDPFixture(socketserver.BaseRequestHandler):
    def handle(self):
        query, conn = self.request
        conn.sendto(dns_reply(query), self.client_address)


class TCPFixture(socketserver.BaseRequestHandler):
    def handle(self):
        size = self.request.recv(2)
        if len(size) != 2:
            return
        remaining = struct.unpack('!H', size)[0]
        query = b''
        while len(query) < remaining:
            query += self.request.recv(remaining - len(query))
        response = dns_reply(query)
        self.request.sendall(struct.pack('!H', len(response)) + response)


def wait_listen(proc, port):
    for _ in range(100):
        if proc.poll() is not None:
            raise AssertionError("native core exited before listener became ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.05):
                return
        except OSError:
            time.sleep(.03)
    raise AssertionError("native core listener timeout")


class LocalDNSIntegration(unittest.TestCase):
    def test_mosdns_policy_and_remote_forward_through_both_native_proxies(self):
        # The production model takes a plain IP for local DNS; test-only port change
        # below points the resulting plugin at a private fixture socket.
        with socketserver.ThreadingUDPServer(("127.0.0.1", 0), UDPFixture) as udp, socketserver.ThreadingTCPServer(("127.0.0.1", 0), TCPFixture) as tcp:
            for server in (udp, tcp):
                Thread(target=server.serve_forever, daemon=True).start()
            try:
                for target in ("mihomo", "sing-box"):
                    with self.subTest(target=target), tempfile.TemporaryDirectory(prefix='yomesh-integration-') as folder:
                        path = Path(folder)
                        proxy_port, dns_port = free_port(), free_port()
                        profile = {"schema_version": 1, "nodes": [], "groups": [], "library": [], "policies": [{"id": "deny", "name": "deny", "type": "DOMAIN-SUFFIX", "value": "ads.example", "target": "REJECT"}, {"id": "local", "name": "local", "type": "DOMAIN-SUFFIX", "value": "local.example", "target": "DIRECT"}], "fallback": "DIRECT", "settings": {"mixed_port": proxy_port, "dns_port": dns_port, "remote_upstream": f"tcp://127.0.0.1:{tcp.server_address[1]}"}}
                        bundle = compile_bundle(profile, target)
                        bundle['files']['mosdns.yaml']['plugins'][0]['args']['upstreams'][0]['addr'] = f"127.0.0.1:{udp.server_address[1]}"
                        for filename, config in bundle['files'].items():
                            write_json(path / filename, config)
                        proxy_binary, dns_binary = find_core(ROOT, target), find_core(ROOT, 'mosdns')
                        proxy_file = path / (target + ('.yaml' if target == 'mihomo' else '.json'))
                        proxy_cmd = [str(proxy_binary), '-d', str(path), '-f', str(proxy_file)] if target == 'mihomo' else [str(proxy_binary), 'run', '-c', str(proxy_file)]
                        processes = []
                        with tempfile.TemporaryFile() as logs:
                            try:
                                processes.append(subprocess.Popen(proxy_cmd, cwd=path, stdout=logs, stderr=logs))
                                wait_listen(processes[-1], proxy_port)
                                processes.append(subprocess.Popen([str(dns_binary), 'start', '-c', str(path / 'mosdns.yaml'), '-d', str(path)], cwd=path, stdout=logs, stderr=logs))
                                wait_listen(processes[-1], dns_port)
                                for hostname, rcode in [('ads.example', 3), ('local.example', 0), ('remote.example', 0)]:
                                    question = b''.join(bytes([len(p)]) + p.encode() for p in hostname.split('.')) + b'\x00\x00\x01\x00\x01'
                                    query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00' + question
                                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                                        client.settimeout(3)
                                        client.sendto(query, ('127.0.0.1', dns_port))
                                        response = client.recv(4096)
                                    self.assertEqual(response[3] & 15, rcode, hostname)
                                    if not rcode:
                                        self.assertEqual(struct.unpack('!H', response[6:8])[0], 1)
                                        self.assertIn(b'\x00\x04\x7f\x00\x00\x2a', response)
                            finally:
                                for process in reversed(processes):
                                    process.terminate()
                                    try:
                                        process.wait(timeout=3)
                                    except subprocess.TimeoutExpired:
                                        process.kill()
                                        process.wait()
            finally:
                for server in (udp, tcp):
                    server.shutdown()


if __name__ == '__main__':
    unittest.main()
