# 完整离线打包与发布

根目录复制 config/local.example.json 为 config/local.json，并填写 projectDir、hubRoot、goExecutable；数据操作还需其他源与工具目录。只有已有内容包的程序组装需要 Python 标准库、Go、前端依赖；不会运行数据解析。

```powershell
npm ci --no-audit --no-fund
yarn package
```

脚本先构建前端，再读取当前运行包 active.json 指向的内容包，编译 Go，生成新的哈希清单。输出必须是项目 products 内全新目录，不替换当前正式包。

可显式指定路径，命令行优先：

```powershell
yarn package -Config '填写本机 JSON 路径' -ContentPack '填写已校验内容包路径' -Output '填写项目 products 内的新目录'
```

包内有 EXE、客户端、packs、defaults、用户配置初值与清单；运行新目录 EXE 验证地址与数据版本。端口被占用时会回退到其他本机端口。将完整目录压 ZIP，不能只发 EXE：

```powershell
$output = '填写新包目录'
python -c 'import shutil,sys; print(shutil.make_archive(sys.argv[1], "zip", sys.argv[2]))' "$output-Windows-x64" $output
```

使用者无需 Node、Go、Python 或游戏。个人配置从旧运行包 config/factions.json 单独保留；数据更新包不会覆盖用户配置。

实包测试：`python scripts/package_checks.py --help`。正式发布：`python scripts/publish.py --help`，需新包专属验收、保护基线和备份目录。此流程不跳过哈希核验，不复用别的包的验收清单。

更新数据使用 Python 工具导出完整数据 ZIP，再在界面校验、安装、回退。数据包不能修改程序代码，React/Go 或兼容格式变化需完整程序包。
