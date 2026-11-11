import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../context/AuthContext';
import { UserPlus, Sparkles, AlertCircle, Loader2 } from 'lucide-react';

const registerSchema = z.object({
  email: z.string().email('Email không đúng định dạng'),
  password: z.string().min(8, 'Mật khẩu phải có ít nhất 8 ký tự'),
  displayName: z.string().min(2, 'Tên hiển thị phải có ít nhất 2 ký tự').max(50, 'Tên quá dài'),
});

export const RegisterPage: React.FC = () => {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [errors, setErrors] = useState<{ email?: string; password?: string; displayName?: string; general?: string }>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrors({});

    const validation = registerSchema.safeParse({ email, password, displayName });
    if (!validation.success) {
      const fieldErrors: { email?: string; password?: string; displayName?: string } = {};
      validation.error.errors.forEach((err) => {
        if (err.path[0] === 'email') fieldErrors.email = err.message;
        if (err.path[0] === 'password') fieldErrors.password = err.message;
        if (err.path[0] === 'displayName') fieldErrors.displayName = err.message;
      });
      setErrors(fieldErrors);
      return;
    }

    setIsSubmitting(true);
    try {
      await register(email, password, displayName);
      navigate('/dashboard', { replace: true });
    } catch (err: any) {
      setErrors({
        general: err?.message || 'Đăng ký không thành công. Email có thể đã tồn tại.',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-slate-950 text-slate-100">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl relative overflow-hidden">
        {/* Glow decoration */}
        <div className="absolute -top-20 -right-20 w-44 h-44 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute -bottom-20 -left-20 w-44 h-44 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />

        <div className="flex items-center gap-2 mb-2 text-indigo-400 font-bold tracking-wider text-sm uppercase">
          <Sparkles className="w-4 h-4" />
          <span>QuizArena Platform</span>
        </div>
        <h1 className="text-3xl font-extrabold text-white tracking-tight mb-2">Tạo Tài Khoản</h1>
        <p className="text-slate-400 text-sm mb-6">
          Đăng ký tài khoản để bắt đầu soạn câu hỏi và làm chủ các trận đấu.
        </p>

        {errors.general && (
          <div className="mb-6 p-3.5 rounded-xl bg-red-500/10 border border-red-500/30 flex items-start gap-3 text-red-400 text-sm">
            <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
            <span>{errors.general}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">
              Tên hiển thị
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Thầy Hưng / QuizMaster"
              className={`w-full px-4 py-2.5 rounded-xl bg-slate-800/80 border text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all ${
                errors.displayName
                  ? 'border-red-500 focus:ring-red-500/30'
                  : 'border-slate-700 focus:border-indigo-500 focus:ring-indigo-500/20'
              }`}
              disabled={isSubmitting}
            />
            {errors.displayName && <p className="text-xs text-red-400 mt-1">{errors.displayName}</p>}
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="host@example.com"
              className={`w-full px-4 py-2.5 rounded-xl bg-slate-800/80 border text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all ${
                errors.email
                  ? 'border-red-500 focus:ring-red-500/30'
                  : 'border-slate-700 focus:border-indigo-500 focus:ring-indigo-500/20'
              }`}
              autoComplete="email"
              disabled={isSubmitting}
            />
            {errors.email && <p className="text-xs text-red-400 mt-1">{errors.email}</p>}
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">
              Mật khẩu (tối thiểu 8 ký tự)
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className={`w-full px-4 py-2.5 rounded-xl bg-slate-800/80 border text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all ${
                errors.password
                  ? 'border-red-500 focus:ring-red-500/30'
                  : 'border-slate-700 focus:border-indigo-500 focus:ring-indigo-500/20'
              }`}
              autoComplete="new-password"
              disabled={isSubmitting}
            />
            {errors.password && <p className="text-xs text-red-400 mt-1">{errors.password}</p>}
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full mt-2 py-3 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white font-medium flex items-center justify-center gap-2 transition-all shadow-lg shadow-indigo-600/25 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                <span>Đang xử lý...</span>
              </>
            ) : (
              <>
                <UserPlus className="w-5 h-5" />
                <span>Đăng ký tài khoản</span>
              </>
            )}
          </button>
        </form>

        <div className="mt-6 pt-6 border-t border-slate-800/80 text-center text-sm">
          <p className="text-slate-400">
            Đã có tài khoản?{' '}
            <Link to="/login" className="text-indigo-400 hover:text-indigo-300 font-medium">
              Đăng nhập ngay
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
};
