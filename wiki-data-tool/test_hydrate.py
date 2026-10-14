import unittest
from types import SimpleNamespace
from refine_costs import hydrate_sources

class HydrateCaseTests(unittest.TestCase):
    def source(self):
        return dict(id='s00001',source_id='mods/test',path='zhCN.SC2Data/LocalizedData/GameHotkeys.txt',object='Button:Move',field='Button/Hotkey/Move',sha256='a'*64,locale='zhCN')
    def test_case_alias_keeps_original_evidence(self):
        source=self.source();record=dict(sha256='a'*64)
        ctx=SimpleNamespace(_files={('mods/test',source['path'].lower()):record})
        data={'sources':[source]};hydrate_sources(ctx,data)
        self.assertIs(ctx.sources[0],source);self.assertIs(ctx._files[('mods/test',source['path'])],record)
    def test_hash_change_is_still_rejected(self):
        source=self.source();ctx=SimpleNamespace(_files={('mods/test',source['path'].lower()):dict(sha256='b'*64)})
        with self.assertRaisesRegex(ValueError,'changed'):hydrate_sources(ctx,{'sources':[source]})
    def test_same_provider_aliases_are_not_ambiguous(self):
        source=self.source();record=dict(sha256='a'*64)
        ctx=SimpleNamespace(_files={('mods/test',source['path'].lower()):record,('mods/test',source['path'].upper()):record})
        hydrate_sources(ctx,{'sources':[source]})
    def test_other_component_cannot_supply_evidence(self):
        source=self.source();ctx=SimpleNamespace(_files={('project:Other',source['path'].lower()):dict(sha256='a'*64)})
        with self.assertRaisesRegex(ValueError,'disappeared'):hydrate_sources(ctx,{'sources':[source]})

if __name__=='__main__':unittest.main()
