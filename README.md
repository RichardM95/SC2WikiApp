# SC2WikiApp

SCORE 百科的 React 前端、Go 本地启动器和 Python 数据工具。源码结构独立于运行包；个人离线使用，无远程运行资源依赖。

## 快速开发

需要 Node.js 22/npm、可选 Yarn；修改 Go 时需要 Go。首次在仓库根目录运行：

```powershell
Set-Location 'E:/Program Files (x86)/SC2WikiApp'
npm ci --no-audit --no-fund
Copy-Item config/local.example.json config/local.json # 仅首次；核对实际路径
python -m pip install -e tools/wiki-data
yarn start
```

启动前先运行已有完整百科的 EXE，作为本机 Go 后端。浏览 http://127.0.0.1:5173，修改 apps/web/src 后实时更新。后端端口变化时在 apps/web/.env.local 设置 SCORE_BACKEND_URL；模板为 apps/web/.env.example。

根命令 yarn start/dev/test/typecheck/build 对应前端开发、测试、类型检查和构建；npm start、npm run dev、npm test、npm run typecheck、npm run build 同样支持。npm 是依赖锁管理方式，Yarn Classic 用于运行脚本，无需第二份 yarn.lock。

```powershell
yarn test
yarn typecheck
yarn build
yarn package # 完整 Windows 包，读取 config/local.json；不会覆盖当前百科
```

## 目录与归属

| 目录 | 用途 |
| --- | --- |
| apps/web | React、Vite、前端测试 |
| apps/launcher | Go 控制窗口、本机服务及测试 |
| tools/wiki-data | 可编辑安装的 Python 解析、生成、导出、审计工具 |
| config | 唯一默认阵营 JSON 和本机目录配置模板 |
| scripts | 完整离线包组装、发布保护、实包检查 |
| docs | 开发、打包、数据流程、接口、视觉及工具链记录 |

默认阵营名、前缀、顺序和显隐修改 config/factions.default.json；打包时写入 defaults/factions.json 并初始化运行包 config/factions.json。后者属于用户，更新时保留。config/local.json 为本机路径，已忽略。

## 操作指南

- [开发与修改](docs/development.md)
- [完整打包、分发与回退](docs/packaging.md)
- [数据更新与审计](docs/data-pipeline.md)
- [接口与内容契约](docs/interfaces.md)
- [界面设计](docs/design.md)

数据版本及兼容校验保留。源码修改不会自动更新正在运行的正式百科。依赖、构建产物、数据、EXE、缓存与私密配置不提交；源码和 SC2 资产授权分别确认。
