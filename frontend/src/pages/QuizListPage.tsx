import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '../services/apiClient';
import { AiGenerateModal } from '../components/AiGenerateModal';
import {
  PlusCircle,
  Sparkles,
  Play,
  Edit,
  Trash2,
  FileQuestion,
  Layers,
  AlertCircle,
  Loader2,
  BookOpen,
} from 'lucide-react';
import type { components } from '../types/api';

type QuizItem = components['schemas']['QuizResponse'];

export const QuizListPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [isAiModalOpen, setIsAiModalOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Lấy danh sách Quiz
  const { data: quizzes, isLoading, error } = useQuery<QuizItem[]>({
    queryKey: ['quizzes'],
    queryFn: () => apiRequest<QuizItem[]>('/api/v1/quizzes'),
  });

  // Mutation tạo trận đấu mới từ Quiz đã xuất bản
  const createGameMutation = useMutation({
    mutationFn: (quizId: string) =>
      apiRequest<components['schemas']['CreateGameResponse']>('/api/v1/games', {
        method: 'POST',
        body: JSON.stringify({ quiz_id: quizId, strategy_id: 'standard' }),
      }),
    onSuccess: (data) => {
      // Chuyển sang trang Host trận đấu
      navigate(`/host/${data.game_id}`, { state: { pin: data.pin, gameId: data.game_id } });
    },
    onError: (err: any) => {
      setActionError(err?.message || 'Không thể tạo phòng đấu. Vui lòng đảm bảo Quiz đã được xuất bản.');
    },
  });

  const handleImportFromAi = async (questions: any[], quizTitle?: string) => {
    // Tạo quiz mới từ các câu hỏi đã duyệt
    try {
      const newQuiz = await apiRequest<components['schemas']['QuizDetailResponse']>('/api/v1/quizzes', {
        method: 'POST',
        body: JSON.stringify({
          title: quizTitle || 'Bộ Quiz Sinh Bằng AI',
          description: `Được tạo tự động từ tài liệu PDF (${questions.length} câu hỏi).`,
          visibility: 'private',
        }),
      });

      // Thêm từng câu hỏi vào Quiz
      for (const q of questions) {
        await apiRequest(`/api/v1/quizzes/${newQuiz.id}/questions`, {
          method: 'POST',
          body: JSON.stringify({
            content: q.content,
            type: q.type,
            time_limit_s: q.time_limit_s,
            points: q.points,
            topic: q.topic,
            explanation: q.explanation,
            options: q.options,
          }),
        });
      }

      await queryClient.invalidateQueries({ queryKey: ['quizzes'] });
      navigate(`/quizzes/${newQuiz.id}/edit`);
    } catch (err: any) {
      setActionError(err?.message || 'Lỗi khi lưu bộ câu hỏi AI vào hệ thống.');
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link to="/dashboard" className="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-600/30">
              Q
            </Link>
            <div>
              <span className="font-extrabold text-lg text-white tracking-tight">Thư Viện Quiz</span>
              <span className="hidden sm:inline-block ml-2 text-xs text-slate-400">
                Quản lý & Soạn thảo
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsAiModalOpen(true)}
              className="py-2 px-3.5 rounded-xl bg-gradient-to-r from-indigo-600 to-cyan-600 hover:from-indigo-500 hover:to-cyan-500 text-white text-sm font-semibold flex items-center gap-2 shadow-lg shadow-indigo-600/20 transition-all"
            >
              <Sparkles className="w-4 h-4" />
              <span className="hidden sm:inline">Sinh bằng AI (PDF)</span>
            </button>
            <Link
              to="/quizzes/new"
              className="py-2 px-3.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-white text-sm font-semibold flex items-center gap-2 border border-slate-700 transition-colors"
            >
              <PlusCircle className="w-4 h-4 text-indigo-400" />
              <span>Soạn Mới</span>
            </Link>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="max-w-7xl mx-auto px-4 py-8">
        {actionError && (
          <div className="mb-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/30 flex items-start justify-between text-red-400 text-sm">
            <div className="flex items-center gap-3">
              <AlertCircle className="w-5 h-5 flex-shrink-0" />
              <span>{actionError}</span>
            </div>
            <button onClick={() => setActionError(null)} className="text-slate-400 hover:text-white">
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        )}

        {isLoading ? (
          <div className="py-20 flex flex-col items-center justify-center text-slate-400 gap-3">
            <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
            <p className="text-sm">Đang tải danh sách Quiz...</p>
          </div>
        ) : error ? (
          <div className="p-6 rounded-2xl bg-red-500/10 border border-red-500/20 text-center text-red-400">
            Không thể tải danh sách Quiz. Vui lòng thử lại sau.
          </div>
        ) : !quizzes || quizzes.length === 0 ? (
          /* Empty state */
          <div className="py-20 flex flex-col items-center justify-center text-center max-w-md mx-auto">
            <div className="w-16 h-16 rounded-3xl bg-slate-900 border border-slate-800 flex items-center justify-center text-slate-500 mb-4 shadow-inner">
              <BookOpen className="w-8 h-8" />
            </div>
            <h3 className="text-lg font-bold text-white mb-1">Chưa Có Bộ Quiz Nào</h3>
            <p className="text-sm text-slate-400 mb-6">
              Bạn có thể tự soạn câu hỏi thủ công hoặc sử dụng Gemini AI để tạo tự động từ tài liệu PDF.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setIsAiModalOpen(true)}
                className="py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm flex items-center gap-2 shadow-lg shadow-indigo-600/25"
              >
                <Sparkles className="w-4 h-4" />
                Sinh bằng AI từ PDF
              </button>
              <Link
                to="/quizzes/new"
                className="py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold text-sm border border-slate-700"
              >
                Tạo thủ công
              </Link>
            </div>
          </div>
        ) : (
          /* Quizzes Grid */
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {quizzes.map((quiz) => {
              const isPublished = quiz.status === 'published';
              const canHost = isPublished && (quiz.question_count ?? 0) > 0;

              return (
                <div
                  key={quiz.id}
                  className="bg-slate-900 border border-slate-800 hover:border-slate-700 rounded-3xl p-6 flex flex-col justify-between transition-all group shadow-lg"
                >
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-3">
                      <span
                        className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold tracking-wide uppercase border ${
                          isPublished
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                            : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                        }`}
                      >
                        {isPublished ? 'Đã Xuất Bản' : 'Bản Nháp'}
                      </span>
                      <span className="text-xs text-slate-500 flex items-center gap-1 font-mono">
                        <Layers className="w-3.5 h-3.5" />
                        v{quiz.version}
                      </span>
                    </div>

                    <h3 className="text-lg font-bold text-white mb-1.5 group-hover:text-indigo-400 transition-colors line-clamp-1">
                      {quiz.title}
                    </h3>
                    <p className="text-xs text-slate-400 line-clamp-2 mb-4 min-h-[32px]">
                      {quiz.description || 'Chưa có mô tả chi tiết cho bộ câu hỏi này.'}
                    </p>

                    <div className="flex items-center gap-4 text-xs text-slate-400 mb-6 py-2 px-3 rounded-xl bg-slate-800/40 border border-slate-700/40">
                      <span className="flex items-center gap-1.5 font-medium text-slate-300">
                        <FileQuestion className="w-4 h-4 text-indigo-400" />
                        {quiz.question_count || 0} câu hỏi
                      </span>
                      <span className="text-slate-600">•</span>
                      <span className="capitalize">{quiz.visibility}</span>
                    </div>
                  </div>

                  <div className="pt-4 border-t border-slate-800/80 flex items-center justify-between gap-2">
                    <Link
                      to={`/quizzes/${quiz.id}/edit`}
                      className="py-2 px-3.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold flex items-center gap-1.5 transition-colors"
                    >
                      <Edit className="w-3.5 h-3.5 text-indigo-400" />
                      <span>Sửa</span>
                    </Link>

                    <button
                      onClick={() => createGameMutation.mutate(quiz.id)}
                      disabled={!canHost || createGameMutation.isPending}
                      className={`py-2 px-4 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-all shadow-md ${
                        canHost
                          ? 'bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white shadow-emerald-600/20'
                          : 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/60'
                      }`}
                      title={!isPublished ? 'Cần xuất bản trước khi mở phòng' : undefined}
                    >
                      {createGameMutation.isPending ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5 fill-current" />
                      )}
                      <span>Mở Phòng Live</span>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* AI Generate Modal */}
      <AiGenerateModal
        isOpen={isAiModalOpen}
        onClose={() => setIsAiModalOpen(false)}
        onImportQuestions={handleImportFromAi}
      />
    </div>
  );
};
