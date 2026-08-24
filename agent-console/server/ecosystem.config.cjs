// PM2 进程配置 — console/server (Next.js 15 App Router)
//
// 本地开发:pm2 守护 `next dev`,保留热重载 + 崩溃自动重启。
// 直接调 Next.js 二进制(而非 `npm run dev`),让 pm2 干净管理进程、信号准确。
//
// 用法:
//   pm2 start ecosystem.config.cjs        # 启动
//   pm2 restart console                    # 重启
//   pm2 reload console                     # 零停机重载(fork 模式下等同 restart)
//   pm2 stop console                       # 停止
//   pm2 delete console                     # 从 pm2 列表移除
//   pm2 logs console                       # 查看日志
//   pm2 monit                              # 实时监控面板
//
// 切换到 prod 模式:先 `npm run build`,再把 args 改为 'start -p 3001'。
module.exports = {
  apps: [
    {
      name: 'console',
      cwd: __dirname,
      script: 'node_modules/next/dist/bin/next',
      args: 'dev -p 3001',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000, // 崩溃后 3s 再拉起,避免疯狂重启
      max_memory_restart: '1G', // 内存超 1G 自动重启(防 next dev 内存泄漏)
      watch: false, // dev 模式 next 自带热重载,无需 pm2 watch
      env: {
        NODE_ENV: 'development',
      },
      out_file: './logs/pm2-out.log',
      error_file: './logs/pm2-error.log',
      merge_logs: true, // 重启时不分割新文件,追加写
      time: true, // 日志加时间戳
    },
  ],
};
