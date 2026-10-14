# 百科数据与发布工具

这里只保留当前流程及其共享解析实现，不再保留旧静态站点或初次迁移脚本。

| 入口 | 用途 |
| --- | --- |
| generate.py plan / generate / snapshot | 源与依赖指纹、受影响批次、增量数据生成、资产基线 |
| export.py build / zip / config | 展示内容包、离线数据更新 ZIP、初始阵营配置 |
| audit.py | 逐事实差异及展示一致性 |
| check_sources.py | 全量来源、哈希和定位 |
| check_release.py | 展示投影、伤害分段、资源校验 |
| build.py | 前端构建、Go 源码和内容包组装为离线程序 |
| package_checks.py | Windows 实包退出、更新及回退检查 |
| publish.py | 通过验收后备份并发布 |

catalog_generate、catalog_adapter、mechanics、priority、navigation、dynamic_text、projection、source_inventory、assets、refine_costs、refine_presentation 是当前流程使用的共享实现。测试文件 test_*.py 覆盖当前机制和发布保护；不代表多个历史版本。

## 路径与数据更新

依赖现有 SC2AgentHub 校验器、SC2EditorTool 解析及资产转换工具、SC2GameData 和游戏数据。所有路径显式指定，源模组只读。每个 CLI 可通过 --help 查看参数。

```powershell
$repo = (Get-Location).Path # 仓库根目录
$tools = "$repo/wiki-data-tool"
$project = 'E:/Program Files (x86)/sc2-agent-hub-data/projects/SCORE-fa82e2d014'
$common = @('--project-dir', $project, '--source-root', 'C:/Users/43732/Desktop/SCORE', '--toolbox-root', 'E:/Program Files (x86)/SC2EditorTool', '--game-root', 'E:/Program Files (x86)/StarCraft II', '--gamedata-root', 'E:/Program Files (x86)/SC2GameData', '--hub-root', 'E:/Program Files (x86)/sc2-agent-hub')
$baseline = '填写当前数据批次'
$next = '填写新的唯一批次'
$legacy = '填写保留的旧专题网站目录' # 用于已有坠毁专题的纯文本提取
python "$tools/generate.py" plan @common --baseline-batch $baseline --report "$project/runtime/reports/$next-plan.json"
python "$tools/generate.py" generate @common --baseline-batch $baseline --batch $next
python "$tools/export.py" build --project-dir $project --batch $next --output "$project/products/SCORE-wiki-content-$next" --source-root 'C:/Users/43732/Desktop/SCORE' --toolbox-root 'E:/Program Files (x86)/SC2EditorTool' --game-root 'E:/Program Files (x86)/StarCraft II' --legacy-site $legacy
python "$tools/audit.py" --project-dir $project --baseline-batch $baseline --batch-suffix $next --hub-root 'E:/Program Files (x86)/sc2-agent-hub' --site-root "$project/products/SCORE-wiki-content-$next" --output-root "$project/runtime/reports/$next-audit"
python "$tools/check_release.py" --project-dir $project --site-root "$project/products/SCORE-wiki-content-$next" --batch-suffix $next --report "$project/runtime/reports/$next-projection.json"
python "$tools/export.py" zip --packet "$project/products/SCORE-wiki-content-$next" --output "$project/products/SCORE-data-update-$next.zip"
python "$tools/generate.py" snapshot @common --baseline-batch $next --asset-manifest "$project/products/SCORE-wiki-content-$next/assets/manifest.json" --report "$project/runtime/reports/score-wiki-$next/source-inventory.json"
```

首次为已有批次记录 snapshot；缺失资产基线时保守重建。仅改程序且源指纹不变可复用数据。所有新数据批次、内容包和报告拒绝覆盖，完整来源、继承和机制证据沿用现有解析。

数据 ZIP 只含清单、JSON 和本地内容资源，不得包含 EXE、HTML、JS、CSS 或用户配置。发布时备份当前包，保存上一份内容以便回退。源数据及格式版本标识保留，不与源码文件命名混淆。

测试可用 `python -m unittest discover -s wiki-data-tool -p 'test_*.py'`；根据实际环境设置 SCORE_TOOLBOX_ROOT、SCORE_HUB_ROOT 等路径。临时输出位于 test-tmp，已由 Git 忽略。
