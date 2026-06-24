import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '智能外呼控制台',
  description: '提示词配置管理 · 多租户 · 版本控制',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="font-sans antialiased bg-slate-50 text-slate-800">{children}</body>
    </html>
  );
}
