import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '../services/apiClient';
import { AiGenerateModal } from '../components/AiGenerateModal';
import {
  ArrowLeft,
  Plus,
  Trash2,
  CheckCircle2,
  Sparkles,
  Save,
  Send,
  AlertCircle,
  HelpCircle,
  Loader2,
} from 'lucide-react';
import type { components } from '../types/api';

type QuestionItem = {
  id?: string;
  content: string;
  type: 'single' | 'multiple' | 'true_false';
  time_limit_s: number;
  points: number;
  topic?: string;
  explanation?: string;
  options: { id?: string; content: string; is_correct: boolean }[];
};

export const QuizEditorPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const isNew = !id || id === 'new';
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<'private' | 'unlisted' | 'public'>('private');
  const [questions, setQuestions] = useState<QuestionItem[]>([]);
  const [isAiModalOpen, setIsAiModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<number>(0);
  const [feedbackMsg, setFeedbackMsg] = useState<{ type: 'error' | 'success'; text: string } | null>(
    null
  );

  // Load Quiz nếu đang sửa
  const { data: quizData, isLoading } = useQuery<components['schemas']['QuizDetailResponse']>({
    queryKey: ['quiz', id],
    queryFn: () => apiRequest<components['schemas']['QuizDetailResponse']>(`/api/v1/quizzes/${id}`),
    enabled: !isNew,
  });

  useEffect(() => {
    if (quizData) {
      setTitle(quizData.title || '');
      setDescription(quizData.description || '');
      setVisibility((quizData.visibility as any) || 'private');
      if (quizData.questions) {
        setQuestions(
          quizData.questions.map((q: any) => ({
            id: q.id,
            content: q.content,
            type: q.type || 'single',
            time_limit_s: q.time_limit_s || 20,
            points: q.points || 1000,
            topic: q.topic || 'Chung',
            explanation: q.explanation || '',
            options: (q.options || []).map((o: any) => ({
              id: o.id,
              content: o.content,
              is_correct: o.is_correct,
            })),
          }))
        );
      }
    }
  }, [quizData]);

  // Thêm một câu hỏi mẫu mới
  const handleAddQuestion = () => {
    const newQ: QuestionItem = {
      content: `Câu hỏi số ${questions.length + 1}?`,
      type: 'single',
      time_limit_s: 20,
      points: 1000,
      topic: 'Chung',
      options: [
        { content: 'Phương án A', is_correct: true },
        { content: 'Phương án B', is_correct: false },
        { content: 'Phương án C', is_correct: false },
        { content: 'Phương án D', is_correct: false },
      ],
    };
    setQuestions([...questions, newQ]);
    setActiveTab(questions.length);
  };

  const handleRemoveQuestion = (idx: number) => {
    const next = questions.filter((_, i) => i !== idx);
    setQuestions(next);
    if (activeTab >= next.length && next.length > 0) {
      setActiveTab(next.length - 1);
    }
  };

  const updateQuestion = (idx: number, updates: Partial<QuestionItem>) => {
    setQuestions((prev) =>
      prev.map((q, i) => {
        if (i !== idx) return q;
        const updated = { ...q, ...updates };
        // Nếu chuyển sang true_false thì chuẩn hóa 2 lựa chọn
        if (updates.type === 'true_false' && q.type !== 'true_false') {
          updated.options = [
            { content: 'Đúng', is_correct: true },
            { content: 'Sai', is_correct: false },
          ];
        }
        return updated;
      })
    );
  };

  const handleOptionChange = (qIdx: number, optIdx: number, content: string) => {
    setQuestions((prev) =>
      prev.map((q, i) => {
        if (i !== qIdx) return q;
        const nextOpts = q.options.map((opt, oI) =>
          oI === optIdx ? { ...opt, content } : opt
        );
        return { ...q, options: nextOpts };
      })
    );
  };

  const handleToggleCorrect = (qIdx: number, optIdx: number) => {
    setQuestions((prev) =>
      prev.map((q, i) => {
        if (i !== qIdx) return q;
        let nextOpts = [];
        if (q.type === 'single' || q.type === 'true_false') {
          nextOpts = q.options.map((opt, oI) => ({
            ...opt,
            is_correct: oI === optIdx,
          }));
        } else {
          nextOpts = q.options.map((opt, oI) =>
            oI === optIdx ? { ...opt, is_correct: !opt.is_correct } : opt
          );
        }
        return { ...q, options: nextOpts };
      })
    );
  };

  const handleAddOption = (qIdx: number) => {
    setQuestions((prev) =>
      prev.map((q, i) => {
        if (i !== qIdx) return q;
        if (q.options.length >= 4) return q;
        return {
          ...q,
          options: [...q.options, { content: `Phương án ${q.options.length + 1}`, is_correct: false }],
        };
      })
    );
  };

  const handleRemoveOption = (qIdx: number, optIdx: number) => {
    setQuestions((prev) =>
      prev.map((q, i) => {
        if (i !== qIdx) return q;
        if (q.options.length <= 2) return q;
        return {
          ...q,
          options: q.options.filter((_, oI) => oI !== optIdx),
        };
      })
    );
  };

  const handleSaveQuiz = async (publish: boolean = false) => {
    setFeedbackMsg(null);
    if (!title.trim()) {
      setFeedbackMsg({ type: 'error', text: 'Vui lòng nhập tiêu đề cho bộ Quiz.' });
      return;
    }

    try {
      let targetQuizId = id;

      if (isNew) {
        // Tạo Quiz
        const created = await apiRequest<components['schemas']['QuizDetailResponse']>('/api/v1/quizzes', {
          method: 'POST',
          body: JSON.stringify({
            title: title.trim(),
            description: description.trim() || undefined,
            visibility,
          }),
        });
        targetQuizId = created.id;
      } else {
        // Cập nhật Quiz thông tin chung
        await apiRequest(`/api/v1/quizzes/${targetQuizId}`, {
          method: 'PATCH',
          body: JSON.stringify({
            title: title.trim(),
            description: description.trim() || undefined,
            visibility,
          }),
        });
      }

      // Lưu/cập nhật từng câu hỏi
      for (const q of questions) {
        if (q.id) {
          await apiRequest(`/api/v1/quizzes/${targetQuizId}/questions/${q.id}`, {
            method: 'PUT',
            body: JSON.stringify({
              content: q.content,
              type: q.type,
              time_limit_s: q.time_limit_s,
              points: q.points,
              topic: q.topic || 'Chung',
              explanation: q.explanation || undefined,
              options: q.options.map((o) => ({ content: o.content, is_correct: o.is_correct })),
            }),
          });
        } else {
          await apiRequest(`/api/v1/quizzes/${targetQuizId}/questions`, {
            method: 'POST',
            body: JSON.stringify({
              content: q.content,
              type: q.type,
              time_limit_s: q.time_limit_s,
              points: q.points,
              topic: q.topic || 'Chung',
              explanation: q.explanation || undefined,
              options: q.options.map((o) => ({ content: o.content, is_correct: o.is_correct })),
            }),
          });
        }
      }

      if (publish) {
        await apiRequest(`/api/v1/quizzes/${targetQuizId}/publish`, {
          method: 'POST',
        });
        setFeedbackMsg({ type: 'success', text: 'Đã xuất bản Quiz thành công! Giờ bạn có thể mở phòng đấu.' });
      } else {
        setFeedbackMsg({ type: 'success', text: 'Đã lưu bản nháp Quiz thành công.' });
      }

      await queryClient.invalidateQueries({ queryKey: ['quizzes'] });
      if (isNew && targetQuizId) {
        navigate(`/quizzes/${targetQuizId}/edit`, { replace: true });
      }
    } catch (err: any) {
      setFeedbackMsg({
        type: 'error',
        text: err?.message || 'Có lỗi xảy ra khi lưu hoặc xuất bản bộ Quiz.',
      });
    }
  };

  const handleImportAiQuestions = (aiQuestions: QuestionItem[]) => {
    setQuestions((prev) => [...prev, ...aiQuestions]);
    setActiveTab(questions.length);
    setFeedbackMsg({
      type: 'success',
      text: `Đã thêm thành công ${aiQuestions.length} câu hỏi do AI sinh vào danh sách!`,
    });
  };

  const curQ = questions[activeTab];

  if (!isNew && isLoading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center text-slate-400 gap-3">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
        <p className="text-sm">Đang tải bộ câu hỏi...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Top Navbar */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              to="/quizzes"
              className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <div>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Nhập tiêu đề Quiz..."
                className="bg-transparent font-extrabold text-lg sm:text-xl text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
              />
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsAiModalOpen(true)}
              className="py-2 px-3.5 rounded-xl bg-gradient-to-r from-indigo-600/20 to-cyan-600/20 hover:from-indigo-600/30 hover:to-cyan-600/30 text-cyan-300 border border-cyan-500/30 text-xs sm:text-sm font-semibold flex items-center gap-2 transition-all"
            >
              <Sparkles className="w-4 h-4 text-cyan-400" />
              <span className="hidden sm:inline">Thêm từ AI (PDF)</span>
            </button>

            <button
              onClick={() => handleSaveQuiz(false)}
              className="py-2 px-3.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs sm:text-sm font-semibold flex items-center gap-1.5 border border-slate-700 transition-colors"
            >
              <Save className="w-4 h-4 text-indigo-400" />
              <span>Lưu nháp</span>
            </button>

            <button
              onClick={() => handleSaveQuiz(true)}
              className="py-2 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs sm:text-sm font-bold flex items-center gap-1.5 shadow-lg shadow-emerald-600/20 transition-all"
            >
              <Send className="w-4 h-4" />
              <span>Xuất Bản</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main editor area */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 flex flex-col gap-6">
        {feedbackMsg && (
          <div
            className={`p-4 rounded-2xl border flex items-start gap-3 text-sm animate-in fade-in ${
              feedbackMsg.type === 'error'
                ? 'bg-red-500/10 border-red-500/30 text-red-400'
                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
            }`}
          >
            {feedbackMsg.type === 'error' ? (
              <AlertCircle className="w-5 h-5 flex-shrink-0" />
            ) : (
              <CheckCircle2 className="w-5 h-5 flex-shrink-0" />
            )}
            <span>{feedbackMsg.text}</span>
          </div>
        )}

        {/* Quiz General Info bar */}
        <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="md:col-span-2">
            <label className="text-xs font-semibold uppercase text-slate-400 block mb-1">
              Mô tả chi tiết
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Mô tả nội dung, mục tiêu của bộ quiz..."
              className="w-full bg-slate-800/60 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold uppercase text-slate-400 block mb-1">
              Chế độ hiển thị
            </label>
            <select
              value={visibility}
              onChange={(e) => setVisibility(e.target.value as any)}
              className="w-full bg-slate-800/60 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              <option value="private">Riêng tư (Private)</option>
              <option value="unlisted">Không công khai (Chỉ ai có link)</option>
              <option value="public">Công khai (Mọi người có thể tìm thấy)</option>
            </select>
          </div>
        </div>

        {/* Questions Manager */}
        <div className="flex-1 grid grid-cols-1 lg:grid-cols-4 gap-6 items-start">
          {/* Left: Questions tabs list */}
          <div className="bg-slate-900 border border-slate-800 rounded-3xl p-4 flex flex-col gap-2 max-h-[70vh] overflow-y-auto">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-1">
              <span className="text-xs font-bold uppercase text-slate-400 tracking-wider">
                Câu hỏi ({questions.length})
              </span>
              <button
                onClick={handleAddQuestion}
                className="p-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
                title="Thêm câu hỏi mới"
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>

            {questions.length === 0 ? (
              <div className="text-center py-8 text-xs text-slate-500">
                Chưa có câu hỏi nào. Bấm nút (+) để thêm câu hỏi mới.
              </div>
            ) : (
              questions.map((q, idx) => (
                <div
                  key={idx}
                  onClick={() => setActiveTab(idx)}
                  className={`p-3 rounded-2xl border text-left cursor-pointer transition-all flex items-center justify-between group ${
                    activeTab === idx
                      ? 'bg-indigo-600/10 border-indigo-500/80 text-white font-medium'
                      : 'bg-slate-800/30 border-slate-700/40 text-slate-400 hover:bg-slate-800/60'
                  }`}
                >
                  <div className="flex items-center gap-2.5 overflow-hidden">
                    <span className="w-6 h-6 rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center text-xs font-mono font-bold text-indigo-400">
                      {idx + 1}
                    </span>
                    <span className="text-xs truncate max-w-[130px]">{q.content}</span>
                  </div>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleRemoveQuestion(idx);
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 hover:text-red-400 transition-opacity"
                    title="Xóa câu hỏi này"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))
            )}
          </div>

          {/* Right: Active Question Detail Form */}
          <div className="lg:col-span-3 bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 space-y-6">
            {!curQ ? (
              <div className="py-20 text-center text-slate-500">
                <HelpCircle className="w-10 h-10 mx-auto mb-2 text-slate-600" />
                <p>Hãy thêm câu hỏi mới hoặc chọn một câu hỏi từ danh sách bên trái để chỉnh sửa.</p>
              </div>
            ) : (
              <>
                {/* Question Content */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-xs font-bold uppercase tracking-wider text-indigo-400">
                      Nội dung câu hỏi {activeTab + 1}
                    </label>
                    <span className="text-xs text-slate-500">{curQ.content.length} ký tự</span>
                  </div>
                  <textarea
                    rows={3}
                    value={curQ.content}
                    onChange={(e) => updateQuestion(activeTab, { content: e.target.value })}
                    placeholder="Nhập nội dung câu hỏi trắc nghiệm..."
                    className="w-full bg-slate-800/70 border border-slate-700 rounded-2xl p-4 text-base text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none font-medium"
                  />
                </div>

                {/* Question Settings Row */}
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 p-4 rounded-2xl bg-slate-800/40 border border-slate-700/50">
                  <div>
                    <label className="text-[11px] font-semibold uppercase text-slate-400 block mb-1">
                      Loại câu hỏi
                    </label>
                    <select
                      value={curQ.type}
                      onChange={(e) => updateQuestion(activeTab, { type: e.target.value as any })}
                      className="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                    >
                      <option value="single">1 Đáp án đúng (Single)</option>
                      <option value="multiple">Nhiều đáp án đúng (Multiple)</option>
                      <option value="true_false">Đúng / Sai (True/False)</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[11px] font-semibold uppercase text-slate-400 block mb-1">
                      Thời gian (Giây)
                    </label>
                    <select
                      value={curQ.time_limit_s}
                      onChange={(e) =>
                        updateQuestion(activeTab, { time_limit_s: Number(e.target.value) })
                      }
                      className="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                    >
                      <option value="10">10 giây</option>
                      <option value="15">15 giây</option>
                      <option value="20">20 giây</option>
                      <option value="30">30 giây</option>
                      <option value="45">45 giây</option>
                      <option value="60">60 giây</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[11px] font-semibold uppercase text-slate-400 block mb-1">
                      Điểm thưởng
                    </label>
                    <select
                      value={curQ.points}
                      onChange={(e) =>
                        updateQuestion(activeTab, { points: Number(e.target.value) })
                      }
                      className="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                    >
                      <option value="500">500 điểm</option>
                      <option value="1000">1000 điểm</option>
                      <option value="2000">2000 điểm (Nhân đôi)</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[11px] font-semibold uppercase text-slate-400 block mb-1">
                      Chủ đề kiến thức
                    </label>
                    <input
                      type="text"
                      value={curQ.topic || ''}
                      onChange={(e) => updateQuestion(activeTab, { topic: e.target.value })}
                      placeholder="VD: Python, Docker..."
                      className="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                </div>

                {/* Options List */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                      Các phương án lựa chọn (Tích chọn đáp án đúng)
                    </span>
                    {curQ.type !== 'true_false' && curQ.options.length < 4 && (
                      <button
                        onClick={() => handleAddOption(activeTab)}
                        className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold flex items-center gap-1"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        <span>Thêm lựa chọn</span>
                      </button>
                    )}
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {curQ.options.map((opt, oIdx) => (
                      <div
                        key={oIdx}
                        className={`p-3 rounded-2xl border flex items-center gap-3 transition-all ${
                          opt.is_correct
                            ? 'bg-emerald-500/10 border-emerald-500/40 shadow-inner'
                            : 'bg-slate-800/40 border-slate-700/60'
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => handleToggleCorrect(activeTab, oIdx)}
                          className={`w-6 h-6 rounded-lg flex items-center justify-center transition-all ${
                            opt.is_correct
                              ? 'bg-emerald-600 text-white shadow-md'
                              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
                          }`}
                          title={opt.is_correct ? 'Đáp án ĐÚNG' : 'Bấm để chọn làm đáp án ĐÚNG'}
                        >
                          <CheckCircle2 className="w-4 h-4" />
                        </button>

                        <input
                          type="text"
                          value={opt.content}
                          onChange={(e) => handleOptionChange(activeTab, oIdx, e.target.value)}
                          placeholder={`Lựa chọn ${oIdx + 1}`}
                          className="flex-1 bg-transparent text-sm text-white placeholder-slate-500 focus:outline-none"
                        />

                        {curQ.type !== 'true_false' && curQ.options.length > 2 && (
                          <button
                            onClick={() => handleRemoveOption(activeTab, oIdx)}
                            className="text-slate-500 hover:text-red-400 p-1"
                            title="Xóa lựa chọn này"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Explanation */}
                <div>
                  <label className="text-xs font-semibold uppercase text-slate-400 block mb-1">
                    Giải thích đáp án (Hiển thị sau khi kết thúc câu hỏi)
                  </label>
                  <input
                    type="text"
                    value={curQ.explanation || ''}
                    onChange={(e) => updateQuestion(activeTab, { explanation: e.target.value })}
                    placeholder="Giải thích ngắn gọn lý do tại sao phương án này là đúng..."
                    className="w-full bg-slate-800/50 border border-slate-700 rounded-xl px-4 py-2.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                </div>
              </>
            )}
          </div>
        </div>
      </main>

      {/* AI Modal */}
      <AiGenerateModal
        isOpen={isAiModalOpen}
        onClose={() => setIsAiModalOpen(false)}
        onImportQuestions={handleImportAiQuestions}
      />
    </div>
  );
};
