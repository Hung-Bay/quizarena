import React, { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Trophy,
  Award,
  AlertTriangle,
  RefreshCw,
  ArrowLeft,
  Users,
  Target,
  BookOpen,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Sparkles,
  BarChart3,
  TrendingUp,
} from 'lucide-react'
import { apiRequest } from '../services/apiClient'
import { SafeMarkdown } from '../components/SafeMarkdown'

interface TopicStat {
  topic: string
  total_questions: number
  accuracy_rate: number
  mastery_level: 'mastered' | 'review_needed' | 'struggling'
}

interface QuestionStat {
  question_index: number
  question_text: string
  topic: string
  correct_percentage: number
  option_distribution: Record<string, number>
}

interface PersonalFeedback {
  player_id?: string
  nickname: string
  final_score: number
  final_rank: number
  total_participants: number
  correct_answers: number
  total_questions: number
  accuracy_rate: number
  performance_level: 'excellent' | 'good' | 'needs_improvement'
  weak_topics: string[]
  advice_list: string[]
  encouragement: string
}

interface GameReportData {
  id: string
  session_id: string
  status: 'ready' | 'degraded' | 'pending' | 'failed'
  stats: {
    total_players: number
    total_questions: number
    average_score: number
    overall_accuracy_rate: number
    topic_stats: TopicStat[]
    question_stats: QuestionStat[]
  }
  summary: string | null
  strengths: string[] | null
  weaknesses: string[] | null
  advice_for_host: string | null
  advice_for_learner: string | null
  personal_feedback: PersonalFeedback | null
  created_at: string
}

export const GameReportPage: React.FC = () => {
  const { gameId } = useParams<{ gameId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'personal' | 'overview'>('personal')
  const [retryMessage, setRetryMessage] = useState<string | null>(null)

  // Fetch báo cáo trận đấu
  const { data: report, isLoading, error } = useQuery<GameReportData>({
    queryKey: ['gameReport', gameId],
    queryFn: () => apiRequest<GameReportData>(`/api/v1/games/${gameId}/report`),
    enabled: !!gameId,
    refetchInterval: (query) => {
      // Nếu report đang ở trạng thái pending thì tự động poll mỗi 3s
      return query.state.data?.status === 'pending' ? 3000 : false
    },
  })

  // Mutation retry phân tích AI (nếu degraded)
  const retryMutation = useMutation({
    mutationFn: () => apiRequest<GameReportData>(`/api/v1/games/${gameId}/report/retry`, { method: 'POST' }),
    onSuccess: (updated) => {
      queryClient.setQueryData(['gameReport', gameId], updated)
      setRetryMessage('Đã gửi yêu cầu phân tích lại AI thành công!')
      setTimeout(() => setRetryMessage(null), 4000)
    },
    onError: (err: any) => {
      setRetryMessage(err.message || 'Không thể thử lại phân tích AI lúc này.')
      setTimeout(() => setRetryMessage(null), 5000)
    },
  })

  // Đổi tab mặc định nếu chỉ có host report (không có personal feedback)
  React.useEffect(() => {
    if (report && !report.personal_feedback) {
      setActiveTab('overview')
    }
  }, [report])

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-950 text-white flex flex-col items-center justify-center p-6">
        <RefreshCw className="w-10 h-10 text-indigo-400 animate-spin mb-4" />
        <h2 className="text-xl font-bold">Đang tổng hợp báo cáo trận đấu...</h2>
        <p className="text-sm text-slate-400 mt-2">Hệ thống đang đối soát dữ liệu và phân tích chỉ số học tập.</p>
      </div>
    )
  }

  if (error || !report) {
    return (
      <div className="min-h-screen bg-slate-950 text-white flex flex-col items-center justify-center p-6">
        <div className="bg-slate-900 border border-red-500/30 rounded-2xl p-8 max-w-md w-full text-center">
          <AlertTriangle className="w-12 h-12 text-red-400 mx-auto mb-4" />
          <h2 className="text-xl font-bold text-red-200">Không tìm thấy báo cáo</h2>
          <p className="text-sm text-slate-400 mt-2 mb-6">
            Báo cáo chưa được khởi tạo hoặc trận đấu chưa kết thúc hoàn toàn.
          </p>
          <button
            onClick={() => navigate('/history')}
            className="w-full py-2.5 px-4 bg-indigo-600 hover:bg-indigo-500 rounded-xl font-medium transition"
          >
            Quay lại lịch sử
          </button>
        </div>
      </div>
    )
  }

  const hasPersonal = !!report.personal_feedback
  const isDegraded = report.status === 'degraded'

  return (
    <div className="min-h-screen bg-slate-950 text-white pb-16">
      {/* Top Header */}
      <header className="sticky top-0 z-30 bg-slate-900/90 backdrop-blur border-b border-slate-800 px-4 lg:px-8 py-4">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/history')}
              className="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-slate-300 transition"
              title="Quay lại lịch sử"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold text-white">Báo Cáo Trận Đấu</h1>
                <span
                  className={`text-xs px-2.5 py-0.5 rounded-full font-semibold uppercase tracking-wider ${
                    report.status === 'ready'
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                      : isDegraded
                      ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                      : 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/40'
                  }`}
                >
                  {report.status === 'ready' ? 'Hoàn chỉnh' : isDegraded ? 'Dự phòng (Degraded)' : report.status}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Mã trận: <span className="font-mono">{report.session_id}</span>
              </p>
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2 self-end sm:self-auto">
            {isDegraded && (
              <button
                onClick={() => retryMutation.mutate()}
                disabled={retryMutation.isPending}
                className="flex items-center gap-1.5 px-3.5 py-2 bg-amber-600/30 hover:bg-amber-600/50 text-amber-200 border border-amber-500/40 rounded-xl text-xs font-semibold transition disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${retryMutation.isPending ? 'animate-spin' : ''}`} />
                Thử lại phân tích AI
              </button>
            )}
            <button
              onClick={() => navigate('/join')}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold transition"
            >
              Vào phòng đấu khác
            </button>
          </div>
        </div>
      </header>

      {/* Thông báo Retry / Alert */}
      {retryMessage && (
        <div className="max-w-7xl mx-auto px-4 lg:px-8 mt-4">
          <div className="p-3 bg-indigo-900/60 border border-indigo-500/50 rounded-xl text-sm text-indigo-200">
            {retryMessage}
          </div>
        </div>
      )}

      {/* Banner nếu Degraded */}
      {isDegraded && (
        <div className="max-w-7xl mx-auto px-4 lg:px-8 mt-4">
          <div className="flex items-start gap-3 p-4 bg-amber-950/40 border border-amber-500/40 rounded-2xl text-amber-200">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-sm">
              <span className="font-bold">Chế độ phân tích dự phòng (Degraded): </span>
              Cổng AI Gemini tạm thời bận hoặc gặp lỗi mạng. Toàn bộ số liệu thống kê lớp học, câu hỏi, phân bố lựa chọn và điểm số vẫn chính xác tuyệt đối 100%.
            </div>
          </div>
        </div>
      )}

      {/* Main Container */}
      <main className="max-w-7xl mx-auto px-4 lg:px-8 mt-6">
        {/* Tab selection (nếu có personal feedback) */}
        {hasPersonal && (
          <div className="flex border-b border-slate-800 mb-6 gap-2">
            <button
              onClick={() => setActiveTab('personal')}
              className={`flex items-center gap-2 py-3 px-5 text-sm font-semibold border-b-2 transition ${
                activeTab === 'personal'
                  ? 'border-indigo-500 text-indigo-400'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <Award className="w-4 h-4" />
              Kết Quả Của Bạn
            </button>
            <button
              onClick={() => setActiveTab('overview')}
              className={`flex items-center gap-2 py-3 px-5 text-sm font-semibold border-b-2 transition ${
                activeTab === 'overview'
                  ? 'border-indigo-500 text-indigo-400'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <BarChart3 className="w-4 h-4" />
              Báo Cáo Toàn Trận
            </button>
          </div>
        )}

        {/* TAB 1: KẾT QUẢ CÁ NHÂN (0 LẦN GỌI AI) */}
        {hasPersonal && activeTab === 'personal' && report.personal_feedback && (
          <div className="space-y-6">
            {/* Top Score Banner */}
            <div className="relative overflow-hidden bg-gradient-to-br from-indigo-950/70 via-slate-900 to-slate-900 border border-indigo-500/30 rounded-3xl p-6 sm:p-8">
              <div className="flex flex-col sm:flex-row items-center justify-between gap-6">
                <div className="text-center sm:text-left">
                  <div className="inline-flex items-center gap-2 px-3 py-1 bg-indigo-500/20 text-indigo-300 rounded-full text-xs font-semibold mb-3">
                    <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                    Nhận xét cá nhân hóa (0-AI Engine)
                  </div>
                  <h2 className="text-2xl sm:text-3xl font-extrabold text-white">
                    Chào, {report.personal_feedback.nickname}!
                  </h2>
                  <p className="text-sm text-slate-400 mt-1">{report.personal_feedback.encouragement}</p>
                </div>

                <div className="flex items-center gap-4">
                  {/* Rank Badge */}
                  <div className="text-center bg-slate-800/80 border border-slate-700/60 rounded-2xl px-5 py-4 min-w-[110px]">
                    <div className="text-2xl font-black text-amber-400 flex items-center justify-center gap-1">
                      {report.personal_feedback.final_rank === 1 && '🥇'}
                      {report.personal_feedback.final_rank === 2 && '🥈'}
                      {report.personal_feedback.final_rank === 3 && '🥉'}
                      #{report.personal_feedback.final_rank}
                    </div>
                    <div className="text-xs text-slate-400 uppercase tracking-wider mt-1">
                      Hạng / {report.personal_feedback.total_participants}
                    </div>
                  </div>

                  {/* Score Badge */}
                  <div className="text-center bg-slate-800/80 border border-slate-700/60 rounded-2xl px-5 py-4 min-w-[110px]">
                    <div className="text-2xl font-black text-emerald-400">
                      {report.personal_feedback.final_score.toLocaleString()}
                    </div>
                    <div className="text-xs text-slate-400 uppercase tracking-wider mt-1">Tổng điểm</div>
                  </div>
                </div>
              </div>

              {/* Progress Summary */}
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mt-6 pt-6 border-t border-slate-800/80">
                <div className="bg-slate-950/40 rounded-xl p-3 text-center">
                  <div className="text-xs text-slate-400">Số câu đúng</div>
                  <div className="text-lg font-bold text-white mt-1">
                    {report.personal_feedback.correct_answers} / {report.personal_feedback.total_questions}
                  </div>
                </div>
                <div className="bg-slate-950/40 rounded-xl p-3 text-center">
                  <div className="text-xs text-slate-400">Tỷ lệ chính xác</div>
                  <div className="text-lg font-bold text-indigo-300 mt-1">
                    {Math.round(report.personal_feedback.accuracy_rate * 100)}%
                  </div>
                </div>
                <div className="col-span-2 sm:col-span-1 bg-slate-950/40 rounded-xl p-3 text-center">
                  <div className="text-xs text-slate-400">Đánh giá chung</div>
                  <div className="text-lg font-bold text-amber-300 mt-1 capitalize">
                    {report.personal_feedback.performance_level === 'excellent'
                      ? 'Xuất sắc'
                      : report.personal_feedback.performance_level === 'good'
                      ? 'Khá giỏi'
                      : 'Cần nỗ lực hơn'}
                  </div>
                </div>
              </div>
            </div>

            {/* Lời khuyên ôn tập cá nhân */}
            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
              <h3 className="text-lg font-bold text-white flex items-center gap-2 mb-4">
                <BookOpen className="w-5 h-5 text-indigo-400" />
                Lời Khuyên Ôn Tập Dành Cho Bạn
              </h3>

              {report.personal_feedback.advice_list && report.personal_feedback.advice_list.length > 0 ? (
                <div className="space-y-3">
                  {report.personal_feedback.advice_list.map((advice, idx) => (
                    <div
                      key={idx}
                      className="flex items-start gap-3 p-3.5 bg-slate-800/50 border border-slate-800 rounded-2xl text-slate-300 text-sm"
                    >
                      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                      <div>{advice}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-4 bg-emerald-950/30 border border-emerald-500/30 rounded-2xl text-emerald-300 text-sm">
                  Bạn đã trả lời xuất sắc tất cả các chủ đề trong trận đấu này! Tiếp tục duy trì phong độ nhé!
                </div>
              )}

              {/* Danh sách chủ đề cần củng cố */}
              {report.personal_feedback.weak_topics && report.personal_feedback.weak_topics.length > 0 && (
                <div className="mt-6 pt-4 border-t border-slate-800">
                  <div className="text-xs text-slate-400 font-semibold uppercase tracking-wider mb-2">
                    Các chủ đề trả lời chưa chính xác:
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {report.personal_feedback.weak_topics.map((t, idx) => (
                      <span
                        key={idx}
                        className="px-3 py-1 bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-semibold rounded-lg"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB 2: BÁO CÁO TOÀN TRẬN / CHO GIẢNG VIÊN */}
        {(!hasPersonal || activeTab === 'overview') && (
          <div className="space-y-6">
            {/* 4 Cards Thống Kê Tổng Quan */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span>Số người tham gia</span>
                  <Users className="w-4 h-4 text-indigo-400" />
                </div>
                <div className="text-2xl font-bold text-white mt-2">
                  {report.stats.total_players}
                </div>
              </div>

              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span>Điểm trung bình</span>
                  <Trophy className="w-4 h-4 text-amber-400" />
                </div>
                <div className="text-2xl font-bold text-white mt-2">
                  {Math.round(report.stats.average_score).toLocaleString()}
                </div>
              </div>

              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span>Tỷ lệ trả lời đúng</span>
                  <Target className="w-4 h-4 text-emerald-400" />
                </div>
                <div className="text-2xl font-bold text-emerald-400 mt-2">
                  {Math.round(report.stats.overall_accuracy_rate * 100)}%
                </div>
              </div>

              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span>Tổng số câu hỏi</span>
                  <HelpCircle className="w-4 h-4 text-cyan-400" />
                </div>
                <div className="text-2xl font-bold text-white mt-2">
                  {report.stats.total_questions}
                </div>
              </div>
            </div>

            {/* AI Insights & Khuyến Nghị */}
            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 space-y-6">
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-indigo-400" />
                Nhận Định Tổng Thể Từ Trợ Lý AI
              </h3>

              {report.summary ? (
                <div className="bg-slate-950/50 border border-slate-800/80 rounded-2xl p-4 text-slate-200">
                  <SafeMarkdown content={report.summary} />
                </div>
              ) : (
                <p className="text-sm text-slate-400 italic">Chưa có tóm tắt chi tiết.</p>
              )}

              {/* Strengths & Weaknesses */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-emerald-950/20 border border-emerald-500/20 rounded-2xl p-4">
                  <h4 className="text-sm font-semibold text-emerald-300 flex items-center gap-1.5 mb-3">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    Điểm Mạnh Của Lớp
                  </h4>
                  {report.strengths && report.strengths.length > 0 ? (
                    <ul className="space-y-2 text-xs text-slate-300">
                      {report.strengths.map((s, idx) => (
                        <li key={idx} className="flex items-start gap-2">
                          <span className="text-emerald-400 font-bold">•</span>
                          <span>{s}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-slate-500">Chưa ghi nhận điểm mạnh đặc thù.</p>
                  )}
                </div>

                <div className="bg-rose-950/20 border border-rose-500/20 rounded-2xl p-4">
                  <h4 className="text-sm font-semibold text-rose-300 flex items-center gap-1.5 mb-3">
                    <XCircle className="w-4 h-4 text-rose-400" />
                    Điểm Cần Khắc Phục
                  </h4>
                  {report.weaknesses && report.weaknesses.length > 0 ? (
                    <ul className="space-y-2 text-xs text-slate-300">
                      {report.weaknesses.map((w, idx) => (
                        <li key={idx} className="flex items-start gap-2">
                          <span className="text-rose-400 font-bold">•</span>
                          <span>{w}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-slate-500">Lớp học không có điểm yếu nghiêm trọng.</p>
                  )}
                </div>
              </div>

              {/* Lời khuyên cho Giảng viên */}
              {report.advice_for_host && (
                <div className="bg-indigo-950/30 border border-indigo-500/30 rounded-2xl p-4">
                  <h4 className="text-sm font-semibold text-indigo-300 flex items-center gap-1.5 mb-2">
                    <TrendingUp className="w-4 h-4 text-indigo-400" />
                    Đề Xuất Hướng Dẫn Tiếp Theo Cho Giảng Viên
                  </h4>
                  <SafeMarkdown content={report.advice_for_host} className="text-xs text-slate-300" />
                </div>
              )}
            </div>

            {/* Phân Tích Mức Độ Thành Thạo Theo Chủ Đề */}
            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
              <h3 className="text-lg font-bold text-white flex items-center gap-2 mb-4">
                <BookOpen className="w-5 h-5 text-indigo-400" />
                Mức Độ Nắm Bắt Kiến Thức Theo Chủ Đề
              </h3>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm text-slate-300">
                  <thead className="bg-slate-950/60 text-xs uppercase text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-3 px-4">Chủ đề</th>
                      <th className="py-3 px-4">Số câu</th>
                      <th className="py-3 px-4">Tỷ lệ đúng</th>
                      <th className="py-3 px-4">Mức độ</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {report.stats.topic_stats?.map((topic, idx) => {
                      const pct = Math.round(topic.accuracy_rate * 100)
                      return (
                        <tr key={idx} className="hover:bg-slate-800/30 transition">
                          <td className="py-3.5 px-4 font-medium text-white">{topic.topic}</td>
                          <td className="py-3.5 px-4 text-slate-400">{topic.total_questions}</td>
                          <td className="py-3.5 px-4">
                            <div className="flex items-center gap-3">
                              <div className="w-24 bg-slate-800 rounded-full h-2 overflow-hidden">
                                <div
                                  className={`h-full rounded-full ${
                                    pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-rose-500'
                                  }`}
                                  style={{ width: `${pct}%` }}
                                />
                              </div>
                              <span className="text-xs font-semibold">{pct}%</span>
                            </div>
                          </td>
                          <td className="py-3.5 px-4">
                            <span
                              className={`text-xs px-2.5 py-1 rounded-full font-semibold ${
                                topic.mastery_level === 'mastered'
                                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                  : topic.mastery_level === 'review_needed'
                                  ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                                  : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                              }`}
                            >
                              {topic.mastery_level === 'mastered'
                                ? 'Đã thành thạo'
                                : topic.mastery_level === 'review_needed'
                                ? 'Cần ôn tập'
                                : 'Đang yếu'}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Chi Tiết Từng Câu Hỏi & Phân Bố Lựa Chọn */}
            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8">
              <h3 className="text-lg font-bold text-white flex items-center gap-2 mb-4">
                <BarChart3 className="w-5 h-5 text-indigo-400" />
                Chi Tiết Phân Bố Trả Lời Từng Câu Hỏi
              </h3>

              <div className="space-y-4">
                {report.stats.question_stats?.map((q, idx) => {
                  const correctPct = Math.round(q.correct_percentage * 100)
                  return (
                    <div
                      key={idx}
                      className="p-4 bg-slate-950/60 border border-slate-800/80 rounded-2xl space-y-3"
                    >
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="w-6 h-6 rounded-lg bg-indigo-500/20 text-indigo-400 text-xs font-bold flex items-center justify-center shrink-0">
                            #{q.question_index + 1}
                          </span>
                          <span className="text-sm font-semibold text-white">{q.question_text}</span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="text-xs text-slate-400">Đúng:</span>
                          <span
                            className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                              correctPct >= 70
                                ? 'bg-emerald-500/20 text-emerald-400'
                                : correctPct >= 40
                                ? 'bg-amber-500/20 text-amber-400'
                                : 'bg-rose-500/20 text-rose-400'
                            }`}
                          >
                            {correctPct}%
                          </span>
                        </div>
                      </div>

                      {/* Phân bố lựa chọn */}
                      {q.option_distribution && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-slate-800/60">
                          {Object.entries(q.option_distribution).map(([opt, count], optIdx) => (
                            <div
                              key={optIdx}
                              className="bg-slate-900/80 border border-slate-800 px-3 py-1.5 rounded-xl flex items-center justify-between text-xs"
                            >
                              <span className="text-slate-400 font-medium truncate max-w-[80px]" title={opt}>
                                {opt}
                              </span>
                              <span className="font-bold text-slate-200">{count} lượt</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
