import React, { useState, useRef } from 'react';
import { tokenStore } from '../services/tokenStore';
import {
  Sparkles,
  Upload,
  FileText,
  X,
  AlertCircle,
  Loader2,
  CheckCircle2,
} from 'lucide-react';
import type { components } from '../types/api';

type QuestionDraft = {
  content: string;
  type: 'single' | 'multiple' | 'true_false';
  time_limit_s: number;
  points: number;
  topic?: string;
  explanation?: string;
  options: { content: string; is_correct: boolean }[];
};

interface AiGenerateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportQuestions: (questions: QuestionDraft[], quizTitle?: string) => void;
}

export const AiGenerateModal: React.FC<AiGenerateModalProps> = ({
  isOpen,
  onClose,
  onImportQuestions,
}) => {
  const [file, setFile] = useState<File | null>(null);
  const [numQuestions, setNumQuestions] = useState(5);
  const [difficulty, setDifficulty] = useState<'easy' | 'medium' | 'hard'>('medium');
  const [language, setLanguage] = useState<'vi' | 'en'>('vi');
  const [notes, setNotes] = useState('');

  const [isProcessing, setIsProcessing] = useState(false);
  const [progressStep, setProgressStep] = useState<string>('');
  const [progressPercent, setProgressPercent] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [generatedQuestions, setGeneratedQuestions] = useState<QuestionDraft[]>([]);
  const [generatedTitle, setGeneratedTitle] = useState<string>('');
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set());

  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!isOpen) return null;

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const selected = e.target.files[0];
      if (selected.type !== 'application/pdf') {
        setErrorMsg('Chỉ chấp nhận tệp định dạng PDF.');
        return;
      }
      if (selected.size > 10 * 1024 * 1024) {
        setErrorMsg('Kích thước tệp vượt quá 10MB.');
        return;
      }
      setErrorMsg(null);
      setFile(selected);
    }
  };

  const handleStartGeneration = async () => {
    if (!file) {
      setErrorMsg('Vui lòng chọn một tệp PDF tài liệu.');
      return;
    }

    setIsProcessing(true);
    setErrorMsg(null);
    setProgressPercent(15);
    setProgressStep('Đang tải tệp PDF lên máy chủ...');

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('num_questions', numQuestions.toString());
      formData.append('difficulty', difficulty);
      formData.append('language', language);
      if (notes.trim()) {
        formData.append('notes', notes.trim());
      }

      const token = tokenStore.getAccessToken();
      const uploadRes = await fetch('/api/v1/ai/quiz-generations', {
        method: 'POST',
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: formData,
      });

      if (!uploadRes.ok) {
        const errJson = await uploadRes.json().catch(() => ({}));
        throw new Error(errJson?.detail || 'Không thể tải lên tài liệu.');
      }

      const enqueueData = (await uploadRes.json()) as components['schemas']['AiJobEnqueueResponse'];
      const jobId = enqueueData.job_id;

      setProgressPercent(35);
      setProgressStep('Đã gửi vào hàng đợi worker. Đang phân tích tài liệu bằng Gemini...');

      // Lắng nghe Server-Sent Events (SSE) với fetch-streaming kèm Bearer token
      const sseRes = await fetch(`/api/v1/ai-jobs/${jobId}/events`, {
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          Accept: 'text/event-stream',
        },
      });

      if (!sseRes.ok || !sseRes.body) {
        throw new Error('Không thể kết nối kênh theo dõi tiến độ SSE.');
      }

      const reader = sseRes.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data:')) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;

            try {
              const event = JSON.parse(dataStr);
              if (event.status === 'running') {
                setProgressPercent(65);
                setProgressStep('AI đang xử lý nội dung và tạo các phương án...');
              } else if (event.status === 'succeeded') {
                setProgressPercent(100);
                setProgressStep('Hoàn tất! Đang nạp danh sách câu hỏi...');

                // Fetch Quiz đã sinh ra để duyệt
                const quizRes = await fetch(`/api/v1/quizzes/${event.quiz_id}`, {
                  headers: {
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                  },
                });

                if (quizRes.ok) {
                  const quizData = await quizRes.json();
                  const questions: QuestionDraft[] = (quizData.questions || []).map((q: any) => ({
                    content: q.content,
                    type: q.type || 'single',
                    time_limit_s: q.time_limit_s || 20,
                    points: q.points || 1000,
                    topic: q.topic || 'Chung',
                    explanation: q.explanation || '',
                    options: (q.options || []).map((o: any) => ({
                      content: o.content,
                      is_correct: o.is_correct,
                    })),
                  }));

                  setGeneratedQuestions(questions);
                  setGeneratedTitle(quizData.title || file.name.replace('.pdf', ''));
                  setSelectedIndices(new Set(questions.map((_, idx) => idx)));
                }
                break;
              } else if (event.status === 'failed') {
                throw new Error(event.error || 'Tác vụ AI thất bại.');
              }
            } catch (err: any) {
              if (err.message) throw err;
            }
          }
        }
      }
    } catch (err: any) {
      setErrorMsg(err?.message || 'Đã xảy ra lỗi khi tạo câu hỏi bằng AI.');
    } finally {
      setIsProcessing(false);
    }
  };

  const toggleSelect = (idx: number) => {
    const next = new Set(selectedIndices);
    if (next.has(idx)) {
      next.delete(idx);
    } else {
      next.add(idx);
    }
    setSelectedIndices(next);
  };

  const handleConfirmImport = () => {
    const approved = generatedQuestions.filter((_, idx) => selectedIndices.has(idx));
    onImportQuestions(approved, generatedTitle);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-3xl max-h-[90vh] bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 shadow-2xl flex flex-col overflow-hidden relative">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-slate-800">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-indigo-600/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">Sinh Câu Hỏi Bằng AI từ PDF</h2>
              <p className="text-xs text-slate-400">
                Tải lên tài liệu giáo trình, slide bài giảng để AI tự động trích xuất câu hỏi.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isProcessing}
            className="p-2 text-slate-400 hover:text-white rounded-xl hover:bg-slate-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content body */}
        <div className="flex-1 overflow-y-auto py-6 space-y-6">
          {errorMsg && (
            <div className="p-4 rounded-2xl bg-red-500/10 border border-red-500/30 flex items-start gap-3 text-red-400 text-sm">
              <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
              <span>{errorMsg}</span>
            </div>
          )}

          {generatedQuestions.length === 0 ? (
            /* Upload & Settings Screen */
            <div className="space-y-6">
              {/* File Dropzone */}
              <div
                onClick={() => fileInputRef.current?.click()}
                className={`border-2 border-dashed rounded-3xl p-8 flex flex-col items-center justify-center cursor-pointer transition-all ${
                  file
                    ? 'border-indigo-500 bg-indigo-500/5'
                    : 'border-slate-700 hover:border-indigo-500/60 hover:bg-slate-800/40'
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  onChange={handleFileChange}
                  className="hidden"
                  disabled={isProcessing}
                />
                {file ? (
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 rounded-2xl bg-indigo-600/20 text-indigo-400 flex items-center justify-center">
                      <FileText className="w-6 h-6" />
                    </div>
                    <div>
                      <p className="font-semibold text-white text-base">{file.name}</p>
                      <p className="text-xs text-slate-400">
                        {(file.size / (1024 * 1024)).toFixed(2)} MB • Bấm để đổi tệp khác
                      </p>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="w-14 h-14 rounded-2xl bg-slate-800 flex items-center justify-center text-slate-400 mb-3">
                      <Upload className="w-7 h-7" />
                    </div>
                    <p className="font-semibold text-slate-200 text-sm">Bấm để chọn tệp tài liệu PDF</p>
                    <p className="text-xs text-slate-500 mt-1">Hỗ trợ tối đa 10 MB, tài liệu học tập, giáo trình</p>
                  </>
                )}
              </div>

              {/* Generation Parameters */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="bg-slate-800/40 border border-slate-700/60 rounded-2xl p-4">
                  <label className="text-xs font-semibold uppercase text-slate-300 block mb-2">
                    Số lượng câu: <span className="text-indigo-400">{numQuestions}</span>
                  </label>
                  <input
                    type="range"
                    min="3"
                    max="15"
                    value={numQuestions}
                    onChange={(e) => setNumQuestions(Number(e.target.value))}
                    disabled={isProcessing}
                    className="w-full accent-indigo-500 cursor-pointer"
                  />
                  <div className="flex justify-between text-[10px] text-slate-500 mt-1">
                    <span>3 câu</span>
                    <span>15 câu</span>
                  </div>
                </div>

                <div className="bg-slate-800/40 border border-slate-700/60 rounded-2xl p-4">
                  <label className="text-xs font-semibold uppercase text-slate-300 block mb-2">
                    Độ khó
                  </label>
                  <select
                    value={difficulty}
                    onChange={(e) => setDifficulty(e.target.value as any)}
                    disabled={isProcessing}
                    className="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="easy">Dễ (Nhận biết)</option>
                    <option value="medium">Trung bình (Hiểu & Vận dụng)</option>
                    <option value="hard">Khó (Phân tích chuyên sâu)</option>
                  </select>
                </div>

                <div className="bg-slate-800/40 border border-slate-700/60 rounded-2xl p-4">
                  <label className="text-xs font-semibold uppercase text-slate-300 block mb-2">
                    Ngôn ngữ
                  </label>
                  <select
                    value={language}
                    onChange={(e) => setLanguage(e.target.value as any)}
                    disabled={isProcessing}
                    className="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="vi">Tiếng Việt</option>
                    <option value="en">English</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold uppercase text-slate-300 block mb-2">
                  Ghi chú trọng tâm cho AI (Tùy chọn)
                </label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Ví dụ: Tập trung vào chương 2 và các câu hỏi thực tế về Docker..."
                  maxLength={300}
                  rows={2}
                  disabled={isProcessing}
                  className="w-full bg-slate-800/60 border border-slate-700 rounded-xl p-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none"
                />
              </div>

              {/* Progress indicator */}
              {isProcessing && (
                <div className="p-5 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 space-y-3">
                  <div className="flex items-center justify-between text-xs font-semibold text-indigo-300">
                    <span className="flex items-center gap-2">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {progressStep}
                    </span>
                    <span>{progressPercent}%</span>
                  </div>
                  <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-indigo-500 to-cyan-500 transition-all duration-300 rounded-full"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          ) : (
            /* Review and Approval Screen */
            <div className="space-y-4">
              <div className="flex items-center justify-between bg-slate-800/60 px-4 py-3 rounded-2xl border border-slate-700/60 text-sm">
                <div>
                  <span className="font-semibold text-white">{generatedTitle}</span>
                  <span className="text-slate-400 ml-2">({generatedQuestions.length} câu hỏi được sinh)</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setSelectedIndices(new Set(generatedQuestions.map((_, idx) => idx)))}
                    className="text-xs text-indigo-400 hover:text-indigo-300 font-medium"
                  >
                    Chọn tất cả
                  </button>
                  <span className="text-slate-600">|</span>
                  <button
                    onClick={() => setSelectedIndices(new Set())}
                    className="text-xs text-slate-400 hover:text-slate-300 font-medium"
                  >
                    Bỏ chọn
                  </button>
                </div>
              </div>

              <div className="space-y-3">
                {generatedQuestions.map((q, idx) => {
                  const isSelected = selectedIndices.has(idx);
                  return (
                    <div
                      key={idx}
                      onClick={() => toggleSelect(idx)}
                      className={`p-4 rounded-2xl border cursor-pointer transition-all ${
                        isSelected
                          ? 'bg-slate-800/80 border-indigo-500/80'
                          : 'bg-slate-900/60 border-slate-800 opacity-60'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <div
                          className={`w-5 h-5 rounded-md flex items-center justify-center mt-0.5 border ${
                            isSelected
                              ? 'bg-indigo-600 border-indigo-500 text-white'
                              : 'border-slate-600 bg-slate-800'
                          }`}
                        >
                          {isSelected && <CheckCircle2 className="w-3.5 h-3.5" />}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-bold text-indigo-400">Câu {idx + 1}</span>
                            <span className="px-2 py-0.5 text-[10px] rounded-full bg-slate-700 text-slate-300 font-medium">
                              {q.topic || 'Chung'}
                            </span>
                            <span className="text-[10px] text-slate-400 font-mono">
                              {q.time_limit_s}s • {q.points}đ
                            </span>
                          </div>
                          <p className="text-sm font-semibold text-white mb-2">{q.content}</p>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                            {q.options.map((opt, oIdx) => (
                              <div
                                key={oIdx}
                                className={`px-3 py-1.5 rounded-lg border ${
                                  opt.is_correct
                                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300 font-medium'
                                    : 'bg-slate-800/40 border-slate-700 text-slate-300'
                                }`}
                              >
                                {opt.content}
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Footer actions */}
        <div className="pt-4 border-t border-slate-800 flex items-center justify-between">
          {generatedQuestions.length === 0 ? (
            <>
              <button
                onClick={onClose}
                disabled={isProcessing}
                className="px-5 py-2.5 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 text-sm font-medium transition-colors"
              >
                Hủy bỏ
              </button>
              <button
                onClick={handleStartGeneration}
                disabled={isProcessing || !file}
                className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-cyan-600 hover:from-indigo-500 hover:to-cyan-500 text-white text-sm font-bold flex items-center gap-2 shadow-lg shadow-indigo-600/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isProcessing ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Đang sinh câu hỏi...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4" />
                    <span>Bắt đầu sinh câu hỏi</span>
                  </>
                )}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setGeneratedQuestions([])}
                className="px-5 py-2.5 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 text-sm font-medium transition-colors"
              >
                &larr; Sinh lại từ tệp khác
              </button>
              <button
                onClick={handleConfirmImport}
                disabled={selectedIndices.size === 0}
                className="px-6 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-bold flex items-center gap-2 shadow-lg shadow-indigo-600/25 transition-all disabled:opacity-50"
              >
                <CheckCircle2 className="w-4 h-4" />
                <span>Thêm {selectedIndices.size} câu vào Quiz</span>
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
