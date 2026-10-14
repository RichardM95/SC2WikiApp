# SC2WikiApp

SCORE 星际争霸 2 百科：React + TypeScript 前端、Go 离线启动器及 Python 数据生成工具。此仓库只保留当前实现，供手动维护和 Windows 离线分发。

## 目录

| 路径 | 用途 |
| --- | --- |
| [wiki-app/app](wiki-app/app) | 前端源码、npm 锁文件及测试 |
| [wiki-app/launcher](wiki-app/launcher) | Go 服务、控制窗口、生命周期、更新与回退 |
| [wiki-app/接口与维护约定.md](wiki-app/接口与维护约定.md) | 内容包和启动器 API |
| [wiki-data-tool](wiki-data-tool) | 当前数据生成、投影、审计及打包流程 |

## 开发

前端：Node.js 22、npm；Go 模块最低 1.22，已验证工具链锁在 wiki-app/go-toolchain-lock.json；数据工具使用现有配置好的 Python 环境。

```powershell
Set-Location wiki-app/app
npm ci --no-audit --no-fund
npm test
npm run build
Set-Location ../launcher
go test ./...
New-Item -ItemType Directory -Force ../../artifacts | Out-Null
go build -trimpath '-ldflags=-s -w -H=windowsgui' -o ../../artifacts/SCORE百科.exe .
```

裸 dist 或 EXE 不含百科内容；完整包的组装方法见 [应用说明](wiki-app/README.md)。Vite 开发服务器不提供 Go API。Python 有效值解析、资产及契约校验仍依赖现有 SC2EditorTool、SC2AgentHub 和本地游戏数据，路径通过参数指定；这些外部工具不复制进本仓库。

日常修改界面使用 wiki-app/app/src，修改启动与更新使用 wiki-app/launcher。阵营默认配置位于 wiki-data-tool/factions.default.json；运行程序的 config/factions.json 是用户配置。编译后的离线使用者无需 Node、Go、Python 或游戏安装目录。

## Git 与发布

依赖、构建产物、EXE、数据包、运行数据和私密配置已由 .gitignore 忽略。源码变化不会自动覆盖运行中的百科；先测试、构建，再按发布保护流程打包。

```bash
git status --short
git add README.md .gitignore .gitattributes wiki-app wiki-data-tool
git diff --cached --stat
git commit -m "Maintain current wiki sources"
git push origin HEAD
```

不在文件名中区分历史实现；数据批次身份、schema_version、client_version 和兼容检查保留，避免混用数据。原产品、数据及备份位于项目空间，不受此源码整理影响。未替维护者指定开源 LICENSE；源码与 SC2 游戏/模组资产的授权需分别确认。
