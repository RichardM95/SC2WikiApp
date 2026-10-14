from pathlib import Path

def repository_root():
    root=Path(__file__).resolve().parents[4]
    if not (root/'config/factions.default.json').is_file():
        raise RuntimeError('请在完整仓库中使用可编辑安装；缺少 config/factions.default.json')
    return root
