import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sc2_wiki_data.cli import main
from sc2_wiki_data.factions import default_config
from sc2_wiki_data.paths import repository_root

class StructureTests(unittest.TestCase):
    def test_default_json_is_the_single_source(self):
        path=repository_root()/'config/factions.default.json'
        configured=json.loads(path.read_text(encoding='utf-8-sig'))
        self.assertEqual(default_config(),configured)
        edited=default_config();edited['factions'][0]['name']='temporary'
        self.assertEqual(default_config(),configured)

    def test_local_paths_are_relative_to_configuration_and_explicit_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'paths.json'
            config.write_text(json.dumps({'projectDir':'relative-project','hubRoot':'relative-hub'}),encoding='utf-8')
            original=sys.argv[:]
            try:
                with patch('sc2_wiki_data.generate.main') as run:
                    main(['generate','plan','--local-config',str(config),'--project-dir','explicit-project'])
                    run.assert_called_once()
                    self.assertEqual(sys.argv[sys.argv.index('--project-dir')+1],'explicit-project')
                    self.assertEqual(sys.argv[sys.argv.index('--hub-root')+1],str((root/'relative-hub').resolve()))
            finally:sys.argv=original

    def test_json_only_export_does_not_receive_build_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'paths.json';config.write_text(json.dumps({'projectDir':'unused'}),encoding='utf-8')
            original=sys.argv[:]
            try:
                with patch('sc2_wiki_data.export.main') as run:
                    main(['export','config','--output','new.json','--local-config',str(config)])
                    run.assert_called_once();self.assertNotIn('--project-dir',sys.argv)
            finally:sys.argv=original

    def test_missing_explicit_config_has_actionable_error(self):
        with self.assertRaisesRegex(SystemExit,'Local configuration not found'):
            main(['generate','--local-config',str(repository_root()/'test-tmp/nonexistent-settings.json')])
