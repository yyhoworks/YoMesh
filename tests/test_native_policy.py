"""Three-step rule editing preserves native matching and independent spaces."""
import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_native_configuration import fixture
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native_configuration import checked, resolve
from yomesh.native_policy import change, decode, overview, prepare, prepare_request
from yomesh.server import handler_for


class NativePolicyTests(unittest.TestCase):
    def test_readable_view_does_not_modify_native_rules(self):
        n = fixture();original = deepcopy(n)
        view = overview(n)
        self.assertEqual(view["policies"][0]["name"], "one")
        self.assertEqual(view["policies"][0]["editor"]["mode"], "rules")
        self.assertEqual(view["policies"][0]["kind"], "direct")
        self.assertTrue(view["policies"][-1]["fallback"])
        self.assertEqual(n, original)

    def test_target_change_preserves_nested_conditions_and_suffix(self):
        n = fixture();text = "AND,((RULE-SET,one),(NOT,((NETWORK,udp)))),直连组,no-resolve,src"
        n["policies"][0]["text"] = text
        updated = change(n, {"action":"target","id":"first","target":"REJECT"})
        self.assertEqual(updated["policies"][0]["text"], text.replace(",直连组,no-resolve,src", ",REJECT,no-resolve,src"))
        self.assertEqual(decode(text)["mode"], "advanced")
        self.assertEqual(n["policies"][0]["text"], text)

    def test_multiple_rules_compile_as_one_or_and_disabled_dependency_skips_whole_rule(self):
        n = fixture();n["mihomo"]["rule-providers"]["two"] = {"type":"inline","behavior":"ipcidr","payload":["192.0.2.0/24"]}
        draft = {"mode":"rules","name":"媒体","rules":["one","two"],"target":"直连组","operator":"or"}
        updated = change(n, {"action":"save","draft":draft})
        self.assertEqual(updated["policies"][-2]["text"], "OR,((RULE-SET,one),(RULE-SET,two)),直连组")
        self.assertEqual(decode(updated["policies"][-2]["text"])["rules"], ["one","two"])
        updated["selection"] = {"rules":["one"]}
        _, config, excluded = resolve(updated)
        self.assertNotIn("媒体", config["rules"])
        self.assertIn(updated["policies"][-2]["id"], [r["id"] for r in excluded])

    def test_no_change_save_and_display_name_do_not_rewrite_native_text(self):
        n = fixture();n["policies"][0]["text"] = "RULE-SET,one,直连组,no-resolve"
        draft = {**decode(n["policies"][0]["text"]),"name":"自定义名称","enabled":True}
        updated = change(n, {"action":"save","id":"first","draft":draft})
        self.assertEqual(updated["policies"][0]["text"], n["policies"][0]["text"])
        checked(updated)
        bundle = compile_bundle({"schema_version":1,"fallback":"DIRECT","native_configuration":updated},"mihomo",rule_output="remote")
        self.assertNotIn("自定义名称", bundle["files"]["mihomo.yaml"])

    def test_matcher_validation_and_explicit_advanced_target(self):
        n = fixture()
        for kind,value in [("IP-CIDR","bad"),("IP-CIDR6","192.0.2.0/24"),("DST-PORT","70000"),("NETWORK","icmp")]:
            with self.assertRaises(ConfigError):prepare(n,{"mode":"matcher","matcher_type":kind,"values":[value],"target":"DIRECT"})
        row = prepare(n,{"mode":"matcher","matcher_type":"DOMAIN-SUFFIX","values":["a.test","b.test"],"target":"DIRECT"})
        self.assertEqual(row["text"],"OR,((DOMAIN-SUFFIX,a.test),(DOMAIN-SUFFIX,b.test)),DIRECT")
        raw = "AND,((NETWORK,udp),(DST-PORT,443)),REJECT"
        row = prepare(n,{"mode":"advanced","text":raw,"target":"DIRECT","set_target":True})
        self.assertEqual(row["text"],raw.removesuffix("REJECT")+"DIRECT")

    def test_fallback_and_reorder_are_guarded(self):
        n = fixture()
        for action in ["toggle","delete","copy"]:
            with self.assertRaises(ConfigError):change(n,{"action":action,"id":"final","enabled":False})
        for ids in [["final","first"],["first","first"],["first"]]:
            with self.assertRaises(ConfigError):change(n,{"action":"reorder","ids":ids})
        copied = change(n,{"action":"copy","id":"first"})
        self.assertEqual(copied["policies"][-1],n["policies"][-1])
        ids=[p["id"] for p in copied["policies"]];ids[0],ids[1]=ids[1],ids[0]
        self.assertEqual([p["id"] for p in change(copied,{"action":"reorder","ids":ids})["policies"]],ids)

    def test_preview_has_no_fetch_and_sources_are_committed_atomically(self):
        n = fixture();original = deepcopy(n)
        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'payload:\n  - +.example.test\n' if self.path=="/ok" else b'not a provider')
            def log_message(self,*args):pass
        http = ThreadingHTTPServer(("127.0.0.1",0),Source)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        url=f"http://127.0.0.1:{http.server_port}"
        request={"action":"save","draft":{"mode":"rules","name":"新规则","rules":[],"target":"DIRECT"},"sources":[{"url":url+"/ok"}]}
        try:
            preview,row=prepare_request(n,request,fetch=False)
            self.assertEqual(n,original)
            self.assertFalse(any(p.startswith('ruleset/user-') for p in preview["documents"]))
            updated=change(n,request)
            self.assertEqual(updated["mihomo"]["rule-providers"]["新规则"]["behavior"],"domain")
            self.assertIn("新规则",updated["policies"][-2]["text"])
            request["sources"].append({"url":url+"/bad"})
            with self.assertRaises(ConfigError):change(n,request)
            self.assertEqual(n,original)
        finally:http.shutdown();http.server_close()

    def test_unselected_group_cannot_be_chosen(self):
        n=fixture();n["selection"]={"groups":[]}
        with self.assertRaises(ConfigError):change(n,{"action":"target","id":"first","target":"直连组新"})
        with self.assertRaises(ConfigError):prepare(n,{"mode":"matcher","matcher_type":"DOMAIN","values":["a.test"],"target":"直连组"})

    def test_http_small_edits_are_revision_checked_and_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            server=ThreadingHTTPServer(("127.0.0.1",0),BaseHTTPRequestHandler);handler=handler_for(Path(directory),server.server_port)
            class Quiet(handler):
                def log_message(self,*args):pass
            server.RequestHandlerClass=Quiet;threading.Thread(target=server.serve_forever,daemon=True).start()
            catalog=handler.space_catalog;old=catalog.workspace_snapshot("default")
            imported=catalog.import_native(fixture(),"测试",catalog.snapshot()["revision"]);ident=imported["id"]
            def call(path,body):
                req=Request(f"http://127.0.0.1:{server.server_port}"+path,json.dumps(body).encode(),headers={"Content-Type":"application/json","X-YoMesh-Space":ident})
                try:
                    with urlopen(req,timeout=5) as response:return response.status,json.load(response)
                except HTTPError as error:return error.code,json.load(error)
            try:
                revision=catalog.workspace_snapshot(ident)["revision"]
                code,state=call('/api/workspace/native-policy',{"action":"target","id":"first","target":"REJECT","revision":revision})
                self.assertEqual(code,200,state)
                self.assertEqual(state["native_view"]["policies"][0]["target"],"REJECT")
                self.assertEqual(call('/api/workspace/native-policy',{"action":"toggle","id":"first","enabled":False,"revision":revision})[0],409)
                self.assertEqual(catalog.workspace_snapshot("default"),old)
            finally:catalog.close();server.shutdown();server.server_close()
