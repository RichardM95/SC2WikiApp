# 开发与修改

## 前端

仓库根执行 npm ci、yarn start。也可用 npm run dev。前端位于 apps/web/src，默认 http://127.0.0.1:5173；既有完整百科作为 http://127.0.0.1:18765 的 Go 后端。

开发代理保持 Host/Origin 及令牌校验，外部来源仍拒绝。修改 apps/web/.env.local 的 SCORE_BACKEND_URL 后重启 Vite。更新和退出按钮操作所连接的后端；调试这些功能时建议复制完整运行包到测试目录。

根目录 yarn test/typecheck/build 对应前端检查；npm 是唯一依赖锁管理方式，锁文件位于根目录。Vite 不自动启动 Go，不修改正式程序。Ctrl+C 停止开发服务器。

## Go

```powershell
Set-Location apps/launcher
go test ./...
go run . --root '填写完整运行包目录' --port 18765 --headless --no-browser
```

运行包必须包含清单、客户端和数据；不要直接运行无资源 EXE。改 Go 后退出旧实例再启动，相同目录会复用服务。Go 未加入 PATH 时用 config/local.json 的 goExecutable 绝对路径调用。

## Python

根目录执行 `python -m pip install -e tools/wiki-data`。共享模块位于 src/sc2_wiki_data，测试位于 tests；外部 Hub/工具箱通过 CLI 或 config/local.json 提供，不靠目录层数猜测外部安装位置。

```powershell
$local = Get-Content config/local.json -Raw | ConvertFrom-Json
$env:SCORE_HUB_ROOT = $local.hubRoot
$env:SCORE_TOOLBOX_ROOT = $local.toolboxRoot
python -m unittest discover -s tools/wiki-data/tests -p 'test_*.py'
python -m unittest discover -s scripts/tests -p 'test_*.py'
python -m sc2_wiki_data --help
```

真实来源回归保留，不以合成样例替代。临时目录 test-tmp 已忽略；数据输出仍在项目空间，源模组只读。

端口占用时可在根目录执行 yarn start --port 5174；不停止其他项目的服务。

