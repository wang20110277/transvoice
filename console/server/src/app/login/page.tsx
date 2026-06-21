'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { authClient } from '@/lib/auth-client';

export default function LoginPage() {
  const [email, setEmail] = useState('admin@transvoice.local');
  const [password, setPassword] = useState('admin123');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    const { error } = await authClient.signIn.email({ email, password });
    setLoading(false);
    if (error) {
      setError(error.message ?? '登录失败');
      return;
    }
    router.push('/prompts');
    router.refresh();
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-100 to-indigo-50 p-4">
      <div className="w-full max-w-sm bg-white rounded-2xl border border-slate-100 shadow-sm p-8 space-y-6">
        <div className="space-y-1 text-center">
          <h1 className="text-lg font-bold text-slate-800">智能外呼管理控制台</h1>
          <p className="text-xs text-slate-500">登录以管理提示词配置</p>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1">
            <label className="text-xs font-semibold text-slate-500">邮箱</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full text-sm p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
              required
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-semibold text-slate-500">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full text-sm p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
              required
            />
          </div>

          {error && <p className="text-xs text-rose-600 bg-rose-50 border border-rose-100 rounded p-2">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-semibold p-2.5 rounded-lg transition-colors"
          >
            {loading ? '登录中…' : '登录'}
          </button>
        </form>

        <div className="text-[11px] text-slate-400 bg-slate-50 rounded-lg p-3 space-y-1 font-mono">
          <p className="font-sans font-semibold text-slate-500">本地测试账号:</p>
          <p>admin@transvoice.local / admin123 (tenant=default)</p>
          <p>fin@transvoice.local / admin123 (tenant=galaxy_fin)</p>
        </div>
      </div>
    </div>
  );
}
