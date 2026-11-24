import React, { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  Trophy,
  History,
  BarChart3,
  Users,
  Calendar,
  ArrowRight,
  ShieldAlert,
  Loader2,
  Gamepad2,
  Flame,
  X,
} from 'lucide-react'
import { apiRequest } from '../services/apiClient'
import { useAuth } from '../context/AuthContext'

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
  status: 'LOBBY' | 'ACTIVE' | 'FINISHED' | 'CANCELLED' | string
  quiz_id: string
  quiz_title: string
  player_count: number
  created_at: string
  started_at: string | null
  ended_at: string | null
}

interface GamePlayerResult {
  player_id: string
  nickname: string
  final_score: number
  final_rank: number
  streak: number
  correct_count: number
  total_answered: number
}

interface GameResultsData {
  game_id: string
  quiz_title: string
  status: string
  total_players: number
  players: GamePlayerResult[]
}

export const HistoryPage: React.FC = () => {
  const navigate = useNavigate()
  const { isAuthenticated, isLoading: authLoading } = useAuth()
  const [activeTab, setActiveTab] = useState<'played' | 'hosted'>('played')

  // Query các trận đã tham gia (Player)
  const {
    data: playedGames,
    isLoading: loadingPlayed,
    error: errorPlayed,
  } = useQuery<PlayedGameItem[]>({
    queryKey: ['myPlayedGames'],
    queryFn: () => apiRequest<PlayedGameItem[]>('/api/v1/me/games'),
    enabled: isAuthenticated,
  })

  // Query các trận đã làm Host
  const {
    data: hostedGames,
    isLoading: loadingHosted,
    error: errorHosted,
  } = useQuery<HostedGameItem[]>({
    queryKey: ['myHostedGames'],
    queryFn: () => apiRequest<HostedGameItem[]>('/api/v1/games'),
    enabled: isAuthenticated,
  })

  // State và Query xem kết quả chi tiết trận đấu
  const [selectedGameId, setSelectedGameId] = useState<string | null>(null)
  const [sortCriteria, setSortCriteria] = useState<'score' | 'streak'>('score')

  const {
    data: gameResults,
    isLoading: loadingResults,
    error: errorResults,
  } = useQuery<GameResultsData>({
    queryKey: ['gameResults', selectedGameId],
    queryFn: () => apiRequest<GameResultsData>(`/api/v1/games/${selectedGameId}/results`),
    enabled: !!selectedGameId,
  })

  // Sắp xếp danh sách người chơi theo tiêu chí đã chọn
  const sortedPlayers = React.useMemo(() => {
    if (!gameResults?.players) return []
    return [...gameResults.players].sort((a, b) => {
      if (sortCriteria === 'streak') {
        if (b.streak !== a.streak) return b.streak - a.streak
        return b.final_score - a.final_score
      }
      if (b.final_score !== a.final_score) return b.final_score - a.final_score
      return b.streak - a.streak
    })
  }, [gameResults?.players, sortCriteria])

  if (authLoading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center text-slate-400 gap-3">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
        <span className="text-sm">Đang tải thông tin phiên...</span>
      </div>
    )
  }

  if (!isAuthenticated) {
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
            <button
              onClick={() => navigate('/')}
              className="w-full py-2.5 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-xl text-slate-400 hover:text-white text-sm font-semibold transition"
            >
              ← Về trang chủ
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
            <button
              onClick={() => navigate('/')}
              className="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-slate-300 transition"
              title="Quay lại Trang chủ"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <Link to="/" className="flex items-center gap-3 hover:opacity-90 transition-opacity">
              <div className="w-10 h-10 rounded-xl bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-indigo-400">
                <History className="w-5 h-5" />
              </div>
              <div>
                <h1 className="text-xl font-bold">Lịch Sử Trận Đấu</h1>
                <p className="text-xs text-slate-400">Theo dõi tiến độ học tập và các trận đấu đã hoàn tất</p>
              </div>
            </Link>
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
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setSelectedGameId(game.game_id)}
                          className="flex items-center gap-1 text-xs font-semibold text-amber-400 hover:text-amber-300 transition cursor-pointer"
                        >
                          <Trophy className="w-3.5 h-3.5" />
                          <span>Xem kết quả</span>
                        </button>
                        <button
                          onClick={() => navigate(`/games/${game.game_id}/report`)}
                          className="flex items-center gap-1 text-xs font-semibold text-indigo-400 hover:text-indigo-300 transition cursor-pointer"
                        >
                          <span>Nhận xét AI</span>
                          <ArrowRight className="w-3.5 h-3.5" />
                        </button>
                      </div>
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

                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setSelectedGameId(game.game_id)}
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/30 text-xs font-semibold text-indigo-300 hover:text-white transition cursor-pointer"
                        >
                          <Trophy className="w-3.5 h-3.5 text-amber-400" />
                          <span>Xem kết quả trận đấu</span>
                        </button>
                        {game.status.toLowerCase() === 'finished' && (
                          <button
                            onClick={() => navigate(`/games/${game.game_id}/report`)}
                            className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-emerald-400 hover:text-emerald-300 transition cursor-pointer"
                            title="Xem báo cáo phân tích lớp học (AI)"
                          >
                            <BarChart3 className="w-3.5 h-3.5" />
                            <span>Báo cáo</span>
                          </button>
                        )}
                      </div>
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

      {/* MODAL XEM KẾT QUẢ TRẬN ĐẤU */}
      {selectedGameId && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 sm:p-6">
          <div className="bg-slate-900 border border-slate-800 rounded-3xl max-w-2xl w-full max-h-[85vh] flex flex-col shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
            {/* Modal Header */}
            <div className="px-6 py-5 border-b border-slate-800 flex items-center justify-between bg-slate-900/90 sticky top-0 z-10">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-2xl bg-amber-500/20 border border-amber-500/30 flex items-center justify-center text-amber-400 shrink-0">
                  <Trophy className="w-5 h-5" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-white leading-tight">
                    Kết Quả Trận Đấu
                  </h2>
                  <p className="text-xs text-slate-400 line-clamp-1">
                    {gameResults?.quiz_title || 'Đang tải thông tin...'}
                  </p>
                </div>
              </div>

              <button
                onClick={() => setSelectedGameId(null)}
                className="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition cursor-pointer"
                title="Đóng"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Toolbar: Bộ lọc & Tiêu chí sắp xếp */}
            <div className="px-6 py-3 bg-slate-950/60 border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-slate-400">Sắp xếp:</span>
                <div className="flex items-center gap-1.5 bg-slate-900 p-1 rounded-xl border border-slate-800">
                  <button
                    onClick={() => setSortCriteria('score')}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition cursor-pointer ${
                      sortCriteria === 'score'
                        ? 'bg-indigo-600 text-white shadow'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <Trophy className="w-3.5 h-3.5" />
                    <span>Điểm số giảm dần</span>
                  </button>
                  <button
                    onClick={() => setSortCriteria('streak')}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition cursor-pointer ${
                      sortCriteria === 'streak'
                        ? 'bg-amber-600 text-white shadow'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <Flame className="w-3.5 h-3.5 text-orange-300" />
                    <span>Streak giảm dần</span>
                  </button>
                </div>
              </div>

              <div className="text-xs text-slate-400 font-semibold">
                Tổng số: <span className="text-indigo-400 font-bold">{sortedPlayers.length}</span> người chơi
              </div>
            </div>

            {/* Danh sách người chơi */}
            <div className="flex-1 overflow-y-auto p-6 space-y-2.5">
              {loadingResults ? (
                <div className="flex flex-col items-center justify-center py-16 text-slate-400">
                  <Loader2 className="w-8 h-8 animate-spin text-indigo-400 mb-3" />
                  <p className="text-sm">Đang tải bảng kết quả...</p>
                </div>
              ) : errorResults ? (
                <div className="p-6 text-center text-red-300 bg-red-500/10 border border-red-500/20 rounded-2xl">
                  <ShieldAlert className="w-8 h-8 mx-auto mb-2 text-red-400" />
                  <p className="text-sm font-semibold">Không thể tải kết quả trận đấu này.</p>
                </div>
              ) : sortedPlayers.length === 0 ? (
                <div className="text-center py-16 text-slate-500">
                  <Users className="w-12 h-12 mx-auto mb-3 text-slate-600" />
                  <p className="text-sm">Không có dữ liệu người chơi trong trận đấu này.</p>
                </div>
              ) : (
                sortedPlayers.map((player, idx) => (
                  <div
                    key={player.player_id}
                    className={`flex items-center justify-between p-3.5 rounded-2xl border transition ${
                      idx === 0
                        ? 'bg-amber-500/10 border-amber-500/30'
                        : idx === 1
                        ? 'bg-slate-400/10 border-slate-400/30'
                        : idx === 2
                        ? 'bg-amber-700/10 border-amber-700/30'
                        : 'bg-slate-800/60 border-slate-800'
                    }`}
                  >
                    {/* Vị trí thứ hạng và tên */}
                    <div className="flex items-center gap-3.5 min-w-0">
                      <div
                        className={`w-8 h-8 rounded-xl flex items-center justify-center text-xs font-black shrink-0 ${
                          idx === 0
                            ? 'bg-amber-500 text-slate-950 shadow-md shadow-amber-500/30'
                            : idx === 1
                            ? 'bg-slate-300 text-slate-950 shadow-md shadow-slate-300/30'
                            : idx === 2
                            ? 'bg-amber-700 text-white shadow-md shadow-amber-700/30'
                            : 'bg-slate-800 text-slate-400 border border-slate-700'
                        }`}
                      >
                        {idx === 0 ? '🥇' : idx === 1 ? '🥈' : idx === 2 ? '🥉' : `#${idx + 1}`}
                      </div>

                      <div className="min-w-0">
                        <p className="font-bold text-white text-sm truncate">{player.nickname}</p>
                        <p className="text-xs text-slate-400">
                          {player.total_answered > 0
                            ? `Đúng ${player.correct_count}/${player.total_answered} câu`
                            : 'Chưa có lượt trả lời'}
                        </p>
                      </div>
                    </div>

                    {/* Điểm và Streak */}
                    <div className="flex items-center gap-4 shrink-0">
                      {/* Streak */}
                      <div className="flex items-center gap-1 px-2.5 py-1 rounded-xl bg-orange-500/10 border border-orange-500/20 text-orange-400 text-xs font-bold">
                        <Flame className="w-3.5 h-3.5 fill-orange-500" />
                        <span>{player.streak} streak</span>
                      </div>

                      {/* Điểm số */}
                      <div className="text-right min-w-[70px]">
                        <span className="text-sm font-black text-emerald-400">
                          {player.final_score.toLocaleString()}
                        </span>
                        <span className="text-[10px] text-slate-500 block font-semibold">pts</span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-4 bg-slate-900 border-t border-slate-800 flex items-center justify-between">
              {gameResults?.status.toLowerCase() === 'finished' ? (
                <button
                  onClick={() => {
                    const gid = selectedGameId
                    setSelectedGameId(null)
                    navigate(`/games/${gid}/report`)
                  }}
                  className="flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold transition cursor-pointer"
                >
                  <BarChart3 className="w-4 h-4" />
                  <span>Xem Báo Cáo Chi Tiết AI</span>
                </button>
              ) : (
                <div />
              )}

              <button
                onClick={() => setSelectedGameId(null)}
                className="px-5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition cursor-pointer"
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
