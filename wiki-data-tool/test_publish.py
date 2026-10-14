import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import publish as p
TMP=Path(__file__).parent/'test-tmp';TMP.mkdir(exist_ok=True)
class PublishTests(unittest.TestCase):
    def fixture(self,root):
        project=Path(root);current=project/'products/SCORE-wiki';new=project/'products/preview';report=project/'runtime/reports';report.mkdir(parents=True)
        current.mkdir(parents=True);new.mkdir()
        def put(f,v):f.parent.mkdir(parents=True,exist_ok=True);f.write_text(json.dumps(v) if isinstance(v,dict) else v,encoding='utf-8')
        put(current/'index.html','old');put(current/'manual.txt','human');put(current/'config/factions.json','custom');put(current/'data/old.json','old data')
        put(current/'manifest.json',{'file_hashes':{'index.html':p.sha(current/'index.html'),'data/old.json':p.sha(current/'data/old.json')}})
        put(new/'index.html','new');put(new/'config/factions.json','default');put(new/'active.json',{'current':'new'})
        put(new/'release-manifest.json',{'batch':'v4-test','file_hashes':{f.relative_to(new).as_posix():p.sha(f) for f in new.rglob('*') if f.is_file()}})
        put(report/'baseline.json',{str(f):p.sha(f) for f in current.rglob('*') if f.is_file()})
        put(report/'acceptance.json',{'validation':'static validation passed','offline_launcher':'passed','browser':'passed','batch':'v4-test','release_manifest_sha256':p.sha(new/'release-manifest.json')})
        return project,current,new,project/'products/backup',report
    def run_publish(self,x):
        project,current,new,backup,r=x;p.publish(project,new,backup,r/'baseline.json',r/'acceptance.json',r/'publication.json')
    def test_backup_config_and_human_content(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            x=self.fixture(d);self.run_publish(x);_,current,_,backup,_=x
            self.assertEqual((current/'config/factions.json').read_text(),'custom');self.assertEqual((current/'manual.txt').read_text(),'human');self.assertFalse((current/'data/old.json').exists());self.assertEqual((backup/'data/old.json').read_text(),'old data')
    def test_rollback_after_final_verify_failure(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            x=self.fixture(d);_,current,_,_,_=x;original=p.verify
            def fail(root,files,skip=()):
                if root==current:raise ValueError('injected failure')
                return original(root,files,skip)
            with patch.object(p,'verify',side_effect=fail),self.assertRaisesRegex(ValueError,'injected'):self.run_publish(x)
            self.assertEqual((current/'index.html').read_text(),'old');self.assertTrue((current/'data/old.json').is_file());self.assertFalse((current/'active.json').exists())
    def test_changed_baseline_rejected_before_backup(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            x=self.fixture(d);x[1].joinpath('manual.txt').write_text('changed')
            with self.assertRaisesRegex(ValueError,'baseline changed'):self.run_publish(x)
            self.assertFalse(x[3].exists())
if __name__=='__main__':unittest.main()
