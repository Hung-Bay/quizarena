import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Trophy,
  History,
  PlayCircle,
  BarChart3,
  Users,
  Calendar,
  ArrowRight,
  ShieldAlert,
  Loader2,
  Gamepad2,
} from 'lucide-react'
import { apiRequest } from '../services/apiClient'
import { tokenStore } from '../services/tokenStore'

interface PlayedGameItem {
  game_id: string
  quiz_title: string
  nickname: string
  final_score: number
  final_rank: number
  ended_at: string | null
}

interface HostedGameItem {
  game_id: string
  pin: string
  status: 'LOBBY' | 'ACTIVE' | 'FINISHED' | 'CANCELLED'
  quiz_id: string
  quiz_title: string
  player_count: number
  created_at: string
  started_at: string | null
  ended_at: string | null
}

export const HistoryPage: React.FC = () => {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<'played' | 'hosted'>('played')
  const isLoggedIn = !!tokenStore.getAccessToken()

  // Query các trận đã tham gia (Player)
  const {
    data: playedGames,
    isLoading: loadingPlayed,
    error: errorPlayed,
  } = useQuery<PlayedGameItem[]>({
    queryKey: ['myPlayedGames'],
    queryFn: () => apiRequest<PlayedGameItem[]>('/api/v1/me/games'),
    enabled: isLoggedIn,
  })

  // Query các trận đã làm Host
  const {
    data: hostedGames,
    isLoading: loadingHosted,
    error: errorHosted,
  } = useQuery<HostedGameItem[]>({
    queryKey: ['myHostedGames'],
    queryFn: () => apiRequest<HostedGameItem[]>('/api/v1/games'),
    enabled: isLoggedIn,
  })

  if (!isLoggedIn) {
    return (
      <div className="min-h-screen bg-slate-950 text-white flex flex-col items-center justify-center p-6">
        <div className="max-w-md w-full bg-slate-900 border border-slate-800 rounded-3xl p-8 text-center space-y-4">
          <div className="w-16 h-16 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl flex items-center justify-center mx-auto text-indigo-400">
            <History className="w-8 h-8" />
          </div>
          <h2 className="text-2xl font-bold">Lịch Sử Trận Đấu</h2>
          <p className="text-sm text-slate-400">
            Đăng nhập để xem lại thành tích các trận bạn đã tham gia hoặc quản lý các trận bạn đã tổ chức.
          </p>
          <div className="flex flex-col gap-2 pt-4">
            <button
              onClick={() => navigate('/login')}
              className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 rounded-xl font-semibold transition"
            >
              Đăng nhập ngay
            </button>
            <button
              onClick={() => navigate('/join')}
              className="w-full py-3 bg-slate-800 hover:bg-slate-700 rounded-xl text-slate-300 font-semibold transition"
            >
              Tham gia bằng mã PIN
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white pb-16">
      {/* Header */}
      <header className="sticky top-0 z-30 bg-slate-900/90 backdrop-blur border-b border-slate-800 px-4 lg:px-8 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-indigo-400">
              <History className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold">Lịch Sử Trận Đấu</h1>
              <p className="text-xs text-slate-400">Theo dõi tiến độ học tập và các trận đấu đã hoàn tất</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate('/quizzes')}
              className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-semibold transition"
            >
              Quản lý Quiz
            </button>
            <button
              onClick={() => navigate('/join')}
              className="px-3.5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold transition"
            >
              Nhập mã PIN
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-6xl mx-auto px-4 lg:px-8 mt-6">
        {/* Navigation Tabs */}
        <div className="flex border-b border-slate-800 mb-6 gap-2">
          <button
            onClick={() => setActiveTab('played')}
            className={`flex items-center gap-2 py-3 px-5 text-sm font-semibold border-b-2 transition ${
              activeTab === 'played'
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Gamepad2 className="w-4 h-4" />
            Trận Đã Tham Gia ({playedGames?.length ?? 0})
          </button>
          <button
            onClick={() => setActiveTab('hosted')}
            className={`flex items-center gap-2 py-3 px-5 text-sm font-semibold border-b-2 transition ${
              activeTab === 'hosted'
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Users className="w-4 h-4" />
            Trận Đã Tổ Chức (Host) ({hostedGames?.length ?? 0})
          </button>
        </div>

        {/* TAB 1: TRẬN ĐÃ THAM GIA */}
        {activeTab === 'played' && (
          <div>
            {loadingPlayed ? (
              <div className="flex flex-col items-center justify-center py-16 text-slate-400">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-400 mb-3" />
                <p className="text-sm">Đang tải lịch sử thi đấu...</p>
              </div>
            ) : errorPlayed ? (
              <div className="bg-slate-900 border border-red-500/30 rounded-2xl p-6 text-center text-red-300">
                <ShieldAlert className="w-8 h-8 mx-auto mb-2 text-red-400" />
                <p>Không thể tải lịch sử tham gia. Vui lòng thử lại sau.</p>
              </div>
            ) : playedGames && playedGames.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {playedGames.map((game) => (
                  <div
                    key={game.game_id}
                    className="bg-slate-900 border border-slate-800 hover:border-slate-700 transition rounded-2xl p-5 flex flex-col justify-between"
                  >
                    <div>
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <h3 className="font-bold text-white text-base leading-snug">{game.quiz_title}</h3>
                        <span className="text-lg font-black text-amber-400 flex items-center gap-0.5 shrink-0">
                          {game.final_rank === 1 && '🥇'}
                          {game.final_rank === 2 && '🥈'}
                          {game.final_rank === 3 && '🥉'}
                          #{game.final_rank}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-xs text-slate-400 mb-4">
                        <span>Biệt danh: <strong className="text-slate-200">{game.nickname}</strong></span>
                        <span>•</span>
                        <span>Điểm: <strong className="text-emerald-400">{game.final_score.toLocaleString()}</strong></span>
                      </div>
                    </div>

                    <div className="flex items-center justify-between pt-3 border-t border-slate-800/80 mt-2">
                      <div className="flex items-center gap-1.5 text-xs text-slate-500">
                        <Calendar className="w-3.5 h-3.5" />
                        <span>
                          {game.ended_at ? new Date(game.ended_at).toLocaleDateString('vi-VN') : 'Đang diễn ra'}
                        </span>
                      </div>
                      <button
                        onClick={() => navigate(`/games/${game.game_id}/report`)}
                        className="flex items-center gap-1 text-xs font-semibold text-indigo-400 hover:text-indigo-300 transition"
                      >
                        <span>Xem nhận xét</span>
                        <ArrowRight className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-slate-900/60 border border-slate-800/60 rounded-3xl p-12 text-center">
                <Trophy className="w-12 h-12 text-slate-600 mx-auto mb-3" />
                <h3 className="text-lg font-bold text-slate-300">Chưa có trận đấu nào</h3>
                <p className="text-sm text-slate-500 max-w-sm mx-auto mt-1 mb-6">
                  Bạn chưa tham gia trận đấu nào. Hãy nhập mã PIN từ Host để trải nghiệm QuizArena!
                </p>
                <button
                  onClick={() => navigate('/join')}
                  className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 rounded-xl font-semibold text-sm transition"
                >
                  Nhập mã PIN ngay
                </button>
              </div>
            )}
          </div>
        )}

        {/* TAB 2: TRẬN ĐÃ TỔ CHỨC (HOST) */}
        {activeTab === 'hosted' && (
          <div>
            {loadingHosted ? (
              <div className="flex flex-col items-center justify-center py-16 text-slate-400">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-400 mb-3" />
                <p className="text-sm">Đang tải danh sách trận tổ chức...</p>
              </div>
            ) : errorHosted ? (
              <div className="bg-slate-900 border border-red-500/30 rounded-2xl p-6 text-center text-red-300">
                <ShieldAlert className="w-8 h-8 mx-auto mb-2 text-red-400" />
                <p>Không thể tải danh sách trận Host (yêu cầu quyền Host). Vui lòng kiểm tra lại tài khoản.</p>
              </div>
            ) : hostedGames && hostedGames.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {hostedGames.map((game) => (
                  <div
                    key={game.game_id}
                    className="bg-slate-900 border border-slate-800 hover:border-slate-700 transition rounded-2xl p-5 flex flex-col justify-between"
                  >
                    <div>
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <h3 className="font-bold text-white text-base leading-snug">{game.quiz_title}</h3>
                        <span
                          className={`text-xs px-2.5 py-0.5 rounded-full font-semibold ${
                            game.status === 'FINISHED'
                              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                              : game.status === 'ACTIVE'
                              ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30'
                              : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                          }`}
                        >
                          {game.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-xs text-slate-400 mb-4">
                        <span>Mã PIN: <strong className="font-mono text-amber-300">{game.pin}</strong></span>
                        <span>•</span>
                        <span>Người tham gia: <strong className="text-slate-200">{game.player_count}</strong></span>
                      </div>
                    </div>

                    <div className="flex items-center justify-between pt-3 border-t border-slate-800/80 mt-2">
                      <div className="flex items-center gap-1.5 text-xs text-slate-500">
                        <Calendar className="w-3.5 h-3.5" />
                        <span>{new Date(game.created_at).toLocaleDateString('vi-VN')}</span>
                      </div>

                      {game.status === 'FINISHED' ? (
                        <button
                          onClick={() => navigate(`/games/${game.game_id}/report`)}
                          className="flex items-center gap-1 text-xs font-semibold text-emerald-400 hover:text-emerald-300 transition"
                        >
                          <BarChart3 className="w-3.5 h-3.5" />
                          <span>Báo cáo lớp học</span>
                        </button>
                      ) : (
                        <button
                          onClick={() => navigate(`/host/${game.game_id}`)}
                          className="flex items-center gap-1 text-xs font-semibold text-indigo-400 hover:text-indigo-300 transition"
                        >
                          <PlayCircle className="w-3.5 h-3.5" />
                          <span>Vào phòng điều khiển</span>
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-slate-900/60 border border-slate-800/60 rounded-3xl p-12 text-center">
                <Users className="w-12 h-12 text-slate-600 mx-auto mb-3" />
                <h3 className="text-lg font-bold text-slate-300">Chưa tổ chức trận nào</h3>
                <p className="text-sm text-slate-500 max-w-sm mx-auto mt-1 mb-6">
                  Bạn có thể chọn một Quiz từ thư viện và bấm "Mở phòng đấu" để bắt đầu làm Host.
                </p>
                <button
                  onClick={() => navigate('/quizzes')}
                  className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 rounded-xl font-semibold text-sm transition"
                >
                  Đến thư viện Quiz
                </button>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}
