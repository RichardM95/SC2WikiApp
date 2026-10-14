# React 前端

启动、修改和打包的完整步骤见 [仓库 README](../../README.md)。

```powershell
npm ci --no-audit --no-fund
yarn start
```

默认前端 http://127.0.0.1:5173，代理到 Go http://127.0.0.1:18765；先启动完整百科的 EXE。修改 app/src 后实时更新。端口改变时复制 .env.example 到 .env.local，设置 SCORE_BACKEND_URL，再重启 Vite。

npm run dev / npm start / yarn dev / yarn start 等价。yarn test、yarn typecheck、yarn build 对应 npm test、npm run typecheck、npm run build。preview 只预览静态构建，不提供 Go API；完整功能用新离线包验证。
