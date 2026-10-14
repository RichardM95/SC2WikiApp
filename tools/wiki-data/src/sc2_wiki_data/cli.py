"""Stable command interface with optional machine-local path defaults."""
import importlib,json,sys
from pathlib import Path
from .paths import repository_root

COMMANDS={'generate':'generate','export':'export','audit':'audit','check-sources':'check_sources','check-release':'check_release'}
PATH_KEYS={'projectDir':'project-dir','hubRoot':'hub-root','sourceRoot':'source-root','toolboxRoot':'toolbox-root','gameRoot':'game-root','gamedataRoot':'gamedata-root'}
SUPPORTED={'generate':set(PATH_KEYS),'export':{'projectDir','sourceRoot','toolboxRoot','gameRoot'},'audit':{'projectDir','hubRoot'},'check-sources':{'projectDir','hubRoot','toolboxRoot'},'check-release':{'projectDir'}}
def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ('-h','--help'):
        print('Usage: python -m sc2_wiki_data <generate|export|audit|check-sources|check-release> [arguments] [--local-config PATH]');return
    command=args.pop(0)
    if command not in COMMANDS:raise SystemExit('Unknown command: '+command)
    explicit=False;config_path=repository_root()/'config/local.json'
    if '--local-config' in args:
        i=args.index('--local-config')
        if i+1>=len(args):raise SystemExit('--local-config requires a path')
        config_path=Path(args[i+1]).resolve();del args[i:i+2];explicit=True
    if config_path.exists():
        config=json.loads(config_path.read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict):raise SystemExit('Local configuration must be a JSON object')
        supported=SUPPORTED[command]
        if command=='export' and args and args[0]!='build':supported=set()
        for key in supported:
            flag='--'+PATH_KEYS[key]
            if flag not in args and not any(a.startswith(flag+'=') for a in args) and key in config:
                value=config[key]
                if not isinstance(value,str) or not value.strip():raise SystemExit('Invalid path: '+key)
                path=Path(value)
                if not path.is_absolute():path=config_path.parent/path
                args.extend((flag,str(path.resolve())))
    elif explicit:raise SystemExit('Local configuration not found: '+str(config_path))
    sys.argv=['sc2_wiki_data '+command,*args]
    return importlib.import_module('.'+COMMANDS[command],__package__).main()
