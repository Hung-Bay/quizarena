import React from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { LogOut, PlusCircle, Play, Shield, User, Sparkles } from 'lucide-react';

export const DashboardPage: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-600/30">
              Q
            </div>
            <div>
              <span className="font-extrabold text-lg text-white tracking-tight">QuizArena</span>
              <span className="hidden sm:inline-block ml-2 px-2 py-0.5 text-xs rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                Phase 4
              </span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="hidden sm:flex items-center gap-2 text-sm text-slate-400 bg-slate-800/60 px-3 py-1.5 rounded-xl border border-slate-700/60">
              <User className="w-4 h-4 text-indigo-400" />
              <span className="font-medium text-slate-200">{user?.display_name || user?.email}</span>
              {user?.roles?.includes('ADMIN') && (
                <Link
                  to="/admin"
                  className="px-2 py-0.5 text-xs font-bold rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 hover:bg-amber-500/30 transition flex items-center gap-1"
                  title="Trang Quản trị Hệ thống"
                >
                  <Shield className="w-3 h-3 text-amber-400" />
                  <span>ADMIN PANEL</span>
                </Link>
              )}
            </div>

            <button
              onClick={handleLogout}
              className="p-2 sm:px-3 sm:py-1.5 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 transition-colors flex items-center gap-2 text-sm"
              title="Đăng xuất"
            >
              <LogOut className="w-4 h-4" />
              <span className="hidden sm:inline">Đăng xuất</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
            Chào mừng trở lại, {user?.display_name || 'Host'}! 👋
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Quản lý bộ đề trắc nghiệm, tạo phòng thi đấu và xem báo cáo thống kê AI.
          </p>
        </div>

        {/* Action cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 hover:border-indigo-500/50 transition-all group">
            <div className="w-12 h-12 rounded-xl bg-indigo-600/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400 mb-4 group-hover:scale-105 transition-transform">
              <PlusCircle className="w-6 h-6" />
            </div>
            <h2 className="text-lg font-bold text-white mb-2">Soạn Quiz Mới</h2>
            <p className="text-slate-400 text-sm mb-4">
              Tạo câu hỏi thủ công hoặc sử dụng AI để sinh câu hỏi từ tài liệu PDF.
            </p>
            <Link
              to="/quizzes/new"
              className="inline-flex items-center gap-2 text-sm font-semibold text-indigo-400 hover:text-indigo-300"
            >
              Bắt đầu soạn câu hỏi &rarr;
            </Link>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 hover:border-emerald-500/50 transition-all group">
            <div className="w-12 h-12 rounded-xl bg-emerald-600/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 mb-4 group-hover:scale-105 transition-transform">
              <Play className="w-6 h-6" />
            </div>
            <h2 className="text-lg font-bold text-white mb-2">Mở Phòng Đấu Live</h2>
            <p className="text-slate-400 text-sm mb-4">
              Khởi tạo phòng đấu thời gian thực với mã PIN và mã QR để người chơi tham gia.
            </p>
            <Link
              to="/quizzes"
              className="inline-flex items-center gap-2 text-sm font-semibold text-emerald-400 hover:text-emerald-300"
            >
              Chọn Quiz để Host &rarr;
            </Link>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 hover:border-cyan-500/50 transition-all group">
            <div className="w-12 h-12 rounded-xl bg-cyan-600/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 mb-4 group-hover:scale-105 transition-transform">
              <Sparkles className="w-6 h-6" />
            </div>
            <h2 className="text-lg font-bold text-white mb-2">Lịch Sử & Báo Cáo AI</h2>
            <p className="text-slate-400 text-sm mb-4">
              Xem nhận xét tự động theo chủ đề, phát hiện lỗ hổng kiến thức và câu hỏi bẫy.
            </p>
            <Link
              to="/history"
              className="inline-flex items-center gap-2 text-sm font-semibold text-cyan-400 hover:text-cyan-300"
            >
              Xem lịch sử trận đấu &rarr;
            </Link>
          </div>
        </div>

        {/* User Info & Security Status */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
          <div className="flex items-center gap-3 mb-4">
            <Shield className="w-5 h-5 text-indigo-400" />
            <h3 className="font-semibold text-white">Trạng Thái An Ninh Phiên Làm Việc</h3>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 text-sm">
            <div className="p-3.5 rounded-xl bg-slate-800/50 border border-slate-700/50">
              <span className="text-slate-400 block text-xs">Vị trí Access Token</span>
              <span className="font-mono text-emerald-400 font-medium">In-Memory Only (An Toàn)</span>
            </div>
            <div className="p-3.5 rounded-xl bg-slate-800/50 border border-slate-700/50">
              <span className="text-slate-400 block text-xs">Cơ chế Refresh Token</span>
              <span className="font-mono text-indigo-400 font-medium">HttpOnly Cookie + Single-Flight</span>
            </div>
            <div className="p-3.5 rounded-xl bg-slate-800/50 border border-slate-700/50">
              <span className="text-slate-400 block text-xs">Vai trò hệ thống</span>
              <span className="font-mono text-cyan-400 font-medium">
                {user?.roles?.join(', ') || 'PLAYER'}
              </span>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
};
