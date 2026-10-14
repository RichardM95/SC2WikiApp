# 项目结构迁移验收

日期：2026-10-14。static validation passed。

- 前端：32 项测试、类型检查和构建通过；根目录 Yarn 启动及参数透传通过。
- Python：114 项数据与结构回归、3 项发布保护回归通过；旧目录移除后再次通过。
- Go：新 apps/launcher 位置回归通过，源码字节保持原样。
- Windows：新结构通过 yarn package 生成完整测试包，13 项真实 EXE 退出、更新、回退、端口及配置保护检查通过。
- 开发代理：实际加载 804 项索引，同源页面连接通过，外部来源仍被拒绝。
- 配置：默认 JSON 原字节保留；打包的 defaults/factions.json 与首次 config/factions.json 均与根配置一致。名称查询遵循用户 JSON，包括掠夺者、骑士团和原显隐设置。
- 源与数据：完整源指纹计划确认 11 批可复用，源模组及正式运行包未替换；Fact、HTTP 接口与内容格式不变。
- 用户修改：迁移前 Git 状态与源码已在仓库外备份；前端、Go、配置和发布脚本的 29 项原字节校验通过。Python 的差异限于包导入、mock 定位、路径、配置去重和已文档化生成子命令的恢复。
- 旧 wiki-app、wiki-data-tool 已移除；默认配置不再位于数据工具目录。只有根 package-lock.json 管理前端依赖。

源码备份：`E:/Program Files (x86)/sc2-agent-hub/tmp/SC2WikiApp-structure-backup`。
完整记录：`E:/Program Files (x86)/sc2-agent-hub-data/projects/SCORE-fa82e2d014/runtime/reports/SC2WikiApp-structure`。
测试程序包：`E:/Program Files (x86)/sc2-agent-hub-data/projects/SCORE-fa82e2d014/products/SC2WikiApp-structure-test`。

Git 未自动暂存、提交或推送。SC2 编辑器及游戏运行验收不在本轮结构迁移范围。
