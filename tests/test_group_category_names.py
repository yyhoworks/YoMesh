"""Display preferences stay outside Profiles and reject stale/invalid changes."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_unified_rule_groups import grouped_fixture
from yomesh.spaces import ConfigurationSpaces
from yomesh.group_categories import DEFAULT_NAMES
from yomesh.model import ConfigError
from yomesh.workspace import Conflict
from yomesh.compiler import compile_bundle


class GroupCategoryNamesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spaces = ConfigurationSpaces(self.root)
        self.ids = [self.spaces.import_native(grouped_fixture(), label, self.spaces.state['revision'])['id'] for label in ('A', 'B')]

    def tearDown(self):
        self.spaces.close()
        self.temp.cleanup()

    def test_names_persist_across_spaces_and_sync_member_labels_without_profile_changes(self):
        before = {i: self.spaces.workspace_snapshot(i)['profile'] for i in self.ids}
        bundle = compile_bundle(before[self.ids[0]], 'mihomo', rule_output='remote')
        result = self.spaces.save_group_category_names({'lines': '机场线路', 'home': '自建线路', 'shared': '常用出口'}, self.spaces.state['revision'])
        self.assertEqual(result['group_category_names']['shared'], '常用出口')
        for ident in self.ids:
            snapshot = self.spaces.workspace_snapshot(ident)
            self.assertEqual(snapshot['profile'], before[ident])
            view = snapshot['native_view']
            for field in ('group_management', 'group_member_choices', 'outlet_choices', 'targets'):
                row = next(r for r in view[field] if r.get('name', r.get('value')) == 'Common')
                self.assertEqual(row['label'], '常用出口 · Common')
        self.assertEqual(compile_bundle(before[self.ids[0]], 'mihomo', rule_output='remote')['files'], bundle['files'])
        self.spaces.close()
        self.spaces = ConfigurationSpaces(self.root)
        self.assertEqual(self.spaces.workspace_snapshot(self.ids[1])['group_category_names']['home'], '自建线路')

    def test_invalid_names_and_stale_revision_do_not_change_saved_preferences(self):
        before = deepcopy(self.spaces.state)
        for value in (None, {}, {**DEFAULT_NAMES, 'extra': 'test'}, {**DEFAULT_NAMES, 'lines': ' '}, {**DEFAULT_NAMES, 'lines': 'x'*13}, {**DEFAULT_NAMES, 'lines': '回家组'}, {**DEFAULT_NAMES, 'home': 'line\nbreak'}):
            with self.assertRaises(ConfigError):
                self.spaces.save_group_category_names(value, self.spaces.state['revision'])
            self.assertEqual(self.spaces.state, before)
        with self.assertRaises(Conflict):
            self.spaces.save_group_category_names(DEFAULT_NAMES, -1)
        self.assertEqual(self.spaces.state, before)

    def test_write_failure_rolls_back_preference_and_catalog_revision(self):
        before = deepcopy(self.spaces.state)
        disk = self.spaces.file.read_bytes()
        with patch.object(self.spaces, '_write', side_effect=OSError('test disk error')):
            with self.assertRaises(OSError):
                self.spaces.save_group_category_names({**DEFAULT_NAMES, 'lines': '机场'}, self.spaces.state['revision'])
        self.assertEqual(self.spaces.state, before)
        self.assertEqual(self.spaces.file.read_bytes(), disk)

    def test_reset_is_explicit_and_portable_workspace_receives_same_display_names(self):
        self.spaces.save_group_category_names({**DEFAULT_NAMES, 'lines': '我的线路'}, self.spaces.state['revision'])
        self.assertEqual(self.spaces.workspace_snapshot('default')['group_category_names']['lines'], '我的线路')
        self.spaces.save_group_category_names(DEFAULT_NAMES, self.spaces.state['revision'])
        self.assertEqual(self.spaces.snapshot()['group_category_names'], DEFAULT_NAMES)
