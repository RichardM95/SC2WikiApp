# SC2WikiApp

SCORE 星际争霸 2 百科：React + TypeScript 前端、Go 本地启动器、Python 数据生成与发布工具。此仓库只保留当前源码，供自己修改及 Windows 离线分发。

## 1. 准备开发环境

| 工具 | 用途 |
| --- | --- |
| Node.js 22 + npm | 前端安装、开发与构建 |
| Yarn（可选） | 可用 yarn start / dev / test / typecheck / build |
| Go | 修改/编译启动器；模块最低 1.22，当前已验证工具链见 wiki-app/go-toolchain-lock.json |
| Python | 完整包组装、数据更新与审计 |

只有修改/生成数据时才需要 SC2EditorTool、SC2GameData 和游戏数据；build.py 组装已有内容包只调用 Python 标准库与 Go 编译器。打包后的使用者仅需 Windows 浏览器。

以下主要使用 **PowerShell**，路径有空格须保留引号。Git Bash 进入仓库可用：

```bash
cd '/e/Program Files (x86)/SC2WikiApp'
```

PowerShell 检查工具：

```powershell
Set-Location 'E:/Program Files (x86)/SC2WikiApp'
node --version
npm --version
yarn --version # 可选
go version
python --version
```

如果 go 不在 PATH，可以将 Go 的 bin 目录加入当前终端 PATH，或使用本机已有的编译器路径：`E:/Program Files (x86)/sc2-agent-hub/tmp/score-wiki-v3/toolchain/go/bin/go.exe`。该路径是已有外部工具链目录，不属于本仓库源码命名。不要把整套 SDK 提交到 Git。

## 2. 运行当前百科（无需重新编译）

```powershell
& 'E:/Program Files (x86)/sc2-agent-hub-data/projects/SCORE-fa82e2d014/products/SCORE-wiki/SCORE百科.exe'
```

控制窗口显示实际地址，通常 http://127.0.0.1:18765。关闭控制窗口/点击退出停止服务；最后页面断开约 30 秒后也会退出。源码目录不是完整运行包，不能只运行没有数据的 dist 或裸 EXE。

## 3. 修改前端并实时预览（支持 Yarn）

**先启动上面的百科作为 Go 后端。** 然后另开终端：

```powershell
Set-Location 'E:/Program Files (x86)/SC2WikiApp/wiki-app/app'
npm ci --no-audit --no-fund
yarn start
```

打开 **http://127.0.0.1:5173**。修改 src 文件后由 Vite 实时更新；当前离线程序本身不会被覆盖。也可以使用：

```powershell
npm run dev
# 或 npm start / yarn dev / yarn start
```

这里用 npm ci 按仓库 package-lock.json 安装，再用 Yarn 运行脚本，两者兼容。当前锁文件以 npm 为准，不需要为了 yarn start 重新安装。若明确改用 Yarn 安装，使用 yarn install 并维护其生成的 yarn.lock；团队应选择一种依赖锁更新方式，避免 npm/Yarn 分别改依赖产生不同解析结果。Yarn 2+ 的 .yarnrc.yml 使用 node-modules 模式，Yarn Classic 默认也使用 node_modules。

如果后端实际端口不是 18765：

```powershell
Copy-Item .env.example .env.local
# 将 .env.local 的 SCORE_BACKEND_URL 改为实际地址，例如 http://127.0.0.1:18766
yarn start
```

修改环境文件后重启 Vite。开发代理仅连接本机 Go，转发 manifest、内容及 API；同源请求的 Host/Origin 与后端匹配，Go 的令牌和来源校验仍保留。数据更新、配置重新加载和页面退出按钮操作的是这个后端的运行包；普通界面调试无需导入数据更新。不要同时把开发页连接到另一份临时后端却误操作正式数据。

5173 被占用时 Vite 会报错；停止之前的开发终端（Ctrl+C）或用 yarn dev --port 5174 指定新端口。后端停止时重新运行百科，不是 React 数据丢失。关闭 Vite 使用 Ctrl+C；Go 使用控制窗口或页面退出。

## 4. 修改哪些文件

| 位置 | 用途 |
| --- | --- |
| wiki-app/app/src/overview.tsx | 阵营导航、分类、搜索 |
| wiki-app/app/src/unit-page.tsx | 单位详情、命令卡、技能与升级 |
| wiki-app/app/src/system-pages.tsx | 数据更新、坠毁系统 |
| wiki-app/app/src/service.tsx | Go 通信、连接和更新通知 |
| wiki-app/app/src/*.css | 星际风格、边框、排版 |
| wiki-app/launcher/*.go | 启动、退出、控制窗口、配置和更新事务 |
| wiki-data-tool/factions.default.json | 新安装的默认阵营配置 |
| 运行包/config/factions.json | 个人阵营配置；保存后“特别设置 → 重新加载阵营配置” |

Go 源码不能热更新；修改后重新编译/打包，并在测试目录运行新包。数据生成逻辑在 wiki-data-tool，步骤见其 [README](wiki-data-tool/README.md)。

## 5. 修改后检查

```powershell
Set-Location 'E:/Program Files (x86)/SC2WikiApp/wiki-app/app'
yarn test
yarn typecheck
yarn build
Set-Location ../launcher
go test ./...
```

npm 对应命令为 npm test、npm run typecheck、npm run build。Go 测试在 Windows 上覆盖控制窗口。前端构建产生 app/dist，默认不纳入 Git。不要以裸 dist 替换现有百科；运行时清单和文件哈希必须与构建一致。

## 6. 只调试 Go（沿用已有数据）

停止使用相同运行目录的旧服务，再从源码目录启动；从窗口/地址确认服务属于哪个目录：

```powershell
Set-Location 'E:/Program Files (x86)/SC2WikiApp/wiki-app/launcher'
go run . --root 'E:/Program Files (x86)/sc2-agent-hub-data/projects/SCORE-fa82e2d014/products/SCORE-wiki' --port 18765 --headless --no-browser
```

此模式使用现有运行包的完整清单/内容，不替换其中的 EXE。Go 修改后退出旧进程再执行 go run；同一目录重复启动会复用实例，可能看不到新代码。更新/回退仍作用于 --root 指定目录。需要隔离时先复制完整运行包到自己的测试目录，并将 --root 指向副本。

## 7. 打包完整 Windows 离线程序

前提：yarn build 已通过，Go 编译器可用。只改程序时复用已有内容包即可。以下自动读取当前 active.json，避免手填历史目录名：

```powershell
$repo = 'E:/Program Files (x86)/SC2WikiApp'
$project = 'E:/Program Files (x86)/sc2-agent-hub-data/projects/SCORE-fa82e2d014'
$runtime = "$project/products/SCORE-wiki"
$active = Get-Content "$runtime/active.json" -Raw | ConvertFrom-Json
$content = "$runtime/packs/$($active.current)"
$goCommand = Get-Command go -ErrorAction SilentlyContinue
$go = if ($goCommand) { $goCommand.Source } else { 'E:/Program Files (x86)/sc2-agent-hub/tmp/score-wiki-v3/toolchain/go/bin/go.exe' }
# 若 go 未在 PATH，替换成实际 Go.exe 的绝对路径
$output = "$project/products/SCORE-wiki-build-$(Get-Date -Format yyyyMMdd-HHmmss)"
python "$repo/wiki-data-tool/build.py" --project-dir $project --output $output --content-pack $content --frontend-dist "$repo/wiki-app/app/dist" --launcher-dir "$repo/wiki-app/launcher" --go-exe $go --default-config "$repo/wiki-data-tool/factions.default.json" --hub-root 'E:/Program Files (x86)/sc2-agent-hub'
& "$output/SCORE百科.exe"
```

输出目录必须不存在且位于该项目 products 内。打包命令复制完整数据资源，编译 Go，生成哈希清单和首次安装配置；暂不自动覆盖当前百科。验证新目录的控制窗口地址，端口占用可能改用 18766–18775。要分发时把输出目录完整压成 ZIP；不要只发 EXE。

ZIP 示例（Python 标准库，可处理完整程序包）：

```powershell
python -c 'import shutil,sys; print(shutil.make_archive(sys.argv[1], "zip", sys.argv[2]))' "$output-Windows-x64" "$output"
```

此 ZIP 解压后根目录直接是 EXE/资源/清单。用户无需任何开发工具即可双击运行。若保留个人配置，验证新包后自行复制原 config/factions.json 到新包，不修改 packs 或 active.json。需要正式替换旧站时使用 publish.py 的完整备份/验收保护流程，参数见 --help。

仅数据变化：按 wiki-data-tool/README.md 生成、审计、export.py zip，然后在程序的“数据更新”界面导入。数据 ZIP 不能更新 React/Go；界面代码或客户端格式变化须发完整程序包。

## 8. Git 与契约

```bash
git status --short
git add README.md .gitignore .gitattributes wiki-app wiki-data-tool
git diff --cached --stat
git commit -m "Update wiki application"
git push origin HEAD
```

依赖、dist、EXE、数据包、运行目录和私密环境文件已忽略；.env.example 可以提交。客户端/数据版本兼容标识保留；修改依赖、接口或格式时同步维护版本和清单。当前 build.py app_version 为 4.0.0，需在正式发布客户端时同步维护，不把文件名清理等同于取消兼容版本。

接口见 [接口与维护约定](wiki-app/接口与维护约定.md)，完整打包入口见 [应用说明](wiki-app/README.md)。源码与 SC2 资产授权分别确认，本仓库未替维护者选择开源 LICENSE。

官方参考：[Vite 开发代理](https://vite.dev/config/server-options#server-proxy)、[Yarn 安装模式](https://yarnpkg.com/features/linkers)。

