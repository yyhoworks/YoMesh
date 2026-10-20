"""The condition list round-trips native rules and shares preview/save semantics."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from test_unified_rule_groups import grouped_fixture, ROOT
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle
from yomesh.native_configuration import resolve
from yomesh.native_policy import change
from yomesh.native_rule_groups import rule_groups, preview
from yomesh.native_rule_conditions import condition_editor


def request_for(native, ident="ai", **changes):
    unit = next(unit for unit in rule_groups(native) if unit["id"] == ident)
    request = {"action": "unit-save", "id": ident, "enabled_dirty": False,
               "draft": {**deepcopy(unit["condition_editor"]), "name": unit["name"], "target": unit["target"], "enabled": unit["enabled"]},
               "outlet": deepcopy(unit["outlet"])}
    request.update(changes)
    return request


class RuleConditionListTests(TestCase):
    def test_mixed_conditions_are_decoded_by_server_and_preserve_original(self):
        native = grouped_fixture()
        native["policies"][0]["text"] = "AND,((RULE-SET,one),(NETWORK,udp),(DST-PORT,443/8443)),AI"
        before = deepcopy(native)
        unit = rule_groups(native)[0]
        self.assertEqual([item["type"] for item in unit["condition_editor"]["conditions"]], ["RULE-SET", "NETWORK", "DST-PORT"])
        self.assertEqual(unit["source_settings"][0]["ref"], "one")
        self.assertEqual(change(native, request_for(native)), before)

    def test_unchanged_parts_keep_flags_and_partial_enablement(self):
        native = grouped_fixture()
        native["policies"][2]["enabled"] = False
        before = deepcopy(native)
        request = request_for(native, "media")
        self.assertEqual([item["no_resolve"] for item in request["draft"]["conditions"]], [False, True])
        self.assertEqual(preview(native, request)["text"], "RULE-SET,domain,Media\nRULE-SET,ip,Media,no-resolve")
        self.assertEqual(change(native, request), before)
        self.assertEqual(native, before)

    def test_partial_enablement_cannot_be_collapsed_to_and(self):
        native = grouped_fixture()
        native["policies"][2]["enabled"] = False
        request = request_for(native, "media")
        request["draft"]["operator"] = "and"
        with self.assertRaisesRegex(ConfigError, "统一启停"):
            change(native, request)

    def test_relationship_change_and_leaf_flags_reach_native_core(self):
        native = grouped_fixture()
        request = request_for(native, "media")
        request["draft"]["operator"] = "and"
        request["draft"]["conditions"].append({"type": "NETWORK", "value": "udp"})
        result = change(native, request)
        self.assertEqual(result["policies"][1]["text"], "AND,((RULE-SET,domain),(RULE-SET,ip,no-resolve),(NETWORK,udp)),Media")
        bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": result}, rule_output="remote", component="proxy")
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_disabled_rule_stays_disabled_on_content_save(self):
        native = grouped_fixture()
        native["policies"][0]["enabled"] = False
        request = request_for(native)
        request["draft"]["enabled"] = True
        request["draft"]["conditions"].append({"type": "DOMAIN-SUFFIX", "value": "other.test"})
        result = change(native, request)
        self.assertFalse(result["policies"][0]["enabled"])

    def test_new_subscription_and_matcher_are_atomic_and_preview_never_fetches(self):
        native = grouped_fixture()
        before = deepcopy(native)
        request = request_for(native)
        request["draft"]["conditions"] = [{"type": "NETWORK", "value": "udp"}, {"type": "RULE-SET", "source": {"name": "fresh", "url": "https://example.test/rules.yaml"}}]
        request["outlet"] = {"type": "select", "members": ["JP"], "default": "JP"}
        with patch("yomesh.native_configuration.refresh_provider") as fetch:
            self.assertEqual(preview(native, request)["text"], "OR,((NETWORK,udp),(RULE-SET,fresh)),AI")
            fetch.assert_not_called()
        with patch("yomesh.native_configuration.refresh_provider", side_effect=ConfigError("source failed")):
            with self.assertRaisesRegex(ConfigError, "source failed"):
                change(native, request)
        self.assertEqual(native, before)
        def downloaded(value, section, ref):
            value = deepcopy(value)
            value["documents"][value["mihomo"][section][ref]["path"]] = "payload:\n- new.test\n"
            return value
        with patch("yomesh.native_configuration.refresh_provider", side_effect=downloaded) as fetch:
            result = change(native, request)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["policies"][0]["text"], "OR,((NETWORK,udp),(RULE-SET,fresh)),AI")
        self.assertEqual(next(g for g in result["mihomo"]["proxy-groups"] if g["name"] == "AI")["proxies"], ["JP"])

    def test_shared_source_changes_use_copy_in_mixed_expression(self):
        native = grouped_fixture()
        native["policies"][0]["text"] = "AND,((RULE-SET,ip,no-resolve),(NETWORK,udp)),AI"
        request = request_for(native, source_edits=[{"ref": "ip", "body": "payload:\n- 198.51.100.0/24\n"}])
        result = change(native, request)
        condition = rule_groups(result)[0]["condition_editor"]["conditions"][0]
        self.assertNotEqual(condition["value"], "ip")
        self.assertTrue(condition["no_resolve"])
        self.assertEqual(result["mihomo"]["rule-providers"]["ip"], native["mihomo"]["rule-providers"]["ip"])
        self.assertEqual(result["policies"][2], native["policies"][2])

    def test_one_leaf_wrapper_and_unknown_native_fields_are_preserved(self):
        native = grouped_fixture()
        native["policies"][0]["text"] = "OR,((RULE-SET,one)),AI"
        native["mihomo"]["proxy-groups"][3]["unknown-field"] = {"keep": True}
        self.assertEqual(change(native, request_for(native)), native)

    def test_nested_or_unknown_conditions_remain_raw(self):
        for text in ("AND,((RULE-SET,one),(NOT,((NETWORK,udp)))),AI,no-resolve,src", "DOMAIN-REGEX,.*\\.test,AI", "invalid"):
            with self.subTest(text=text):
                self.assertEqual(condition_editor(text), {"mode": "advanced", "text": text})
        native = grouped_fixture()
        native["policies"][0]["text"] = "AND,((RULE-SET,one),(NOT,((NETWORK,udp)))),AI,no-resolve,src"
        self.assertEqual(change(native, request_for(native)), native)

    def test_turning_off_dependency_skips_entire_mixed_policy(self):
        native = grouped_fixture()
        request = request_for(native)
        request["draft"]["conditions"].append({"type": "NETWORK", "value": "udp"})
        result = change(native, request)
        result["selection"] = {"rules": ["domain", "ip"]}
        _, config, excluded = resolve(result)
        self.assertNotIn(result["policies"][0]["text"], config["rules"])
        self.assertIn("ai", [item["id"] for item in excluded])

    def test_malformed_conditions_are_rejected_before_writing(self):
        native = grouped_fixture()
        before = deepcopy(native)
        invalid = [[], [{"type": []}], [{"type": "NETWORK", "value": "icmp"}], [{"type": "DST-PORT", "value": "70000"}], [{"type": "DOMAIN", "value": "a\n.test"}], [{"type": "RULE-SET", "value": "missing"}], [{"type": "RULE-SET", "source": {"url": []}}], [{"type": "NETWORK", "value": "udp", "no_resolve": "true"}], [{"type": "DOMAIN", "value": "same.test"}] * 2]
        for conditions in invalid:
            with self.subTest(conditions=conditions):
                request = request_for(native)
                request["draft"]["conditions"] = conditions
                with self.assertRaises(ConfigError):
                    change(native, request)
                self.assertEqual(native, before)
