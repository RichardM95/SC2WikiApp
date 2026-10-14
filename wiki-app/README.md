# 百科应用

app/ 为 React 前端，launcher/ 为 Go 启动器。[接口与维护约定](接口与维护约定.md) 描述当前 API 与内容格式，[design.md](design.md) 描述界面规范。

前端执行 npm test、npm run build；Go 执行 go test ./...。程序版本由 app/package.json、构建清单及启动器兼容要求维护，保留正常语义版本。

## 组装完整包

在仓库根目录运行，使用已校验的内容包和新的输出目录：

```powershell
$repo = (Get-Location).Path
$project = 'E:/Program Files (x86)/sc2-agent-hub-data/projects/SCORE-fa82e2d014'
$hub = 'E:/Program Files (x86)/sc2-agent-hub'
$content = '填写已校验内容包的绝对路径'
$output = "$project/products/SCORE-wiki-preview-填写唯一批次"
$go = (Get-Command go).Source
python "$repo/wiki-data-tool/build.py" --project-dir $project --output $output --content-pack $content --frontend-dist "$repo/wiki-app/app/dist" --launcher-dir "$repo/wiki-app/launcher" --go-exe $go --default-config "$repo/wiki-data-tool/factions.default.json" --hub-root $hub
```

先完成 npm ci 和 npm run build，再运行上述命令。生成器拒绝覆盖已有输出。启动新目录中的 EXE 验证，不以裸 dist 覆盖当前程序。build.py 当前客户端版本为 4.0.0；客户端发布时应同步维护程序版本及清单，格式变化还需更新兼容要求。

正式更新由 wiki-data-tool/publish.py 完成，要求新包专属验收报告和保护基线，先备份旧站并保留用户配置。数据更新通过界面导入完整数据 ZIP；程序升级使用完整程序包。
