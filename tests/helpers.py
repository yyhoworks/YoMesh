"""Synthetic fixtures shared by setup and HTTP integration tests."""
from yomesh.subscriptions import normalize


def fetched_fixture(source):
    nodes = [normalize({"name": name, "protocol": "trojan", "server": "node.example.test", "port": 443 + index, "password": "synthetic-fixture-password"}, source["id"])
             for index, name in enumerate(("HK 香港 fixture", "US 美国 fixture", "SG 新加坡 fixture"))]
    return nodes, {"total": len(nodes), "retained": len(nodes), "filtered": 0, "skipped": 0}
