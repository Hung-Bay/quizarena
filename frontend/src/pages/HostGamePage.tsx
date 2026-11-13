import React, { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import { useGameSocket } from "../hooks/useGameSocket";
import {
  Users,
  Play,
  SkipForward,
  StopCircle,
  Trophy,
  Crown,
  Medal,
  UserX,
  Maximize2,
  Minimize2,
  Clock,
  CheckCircle2,
  Sparkles,
  BarChart3,
} from "lucide-react";

const COLOR_CLASSES = [
  {
    bg: "bg-rose-600",
    text: "text-rose-100",
    border: "border-rose-400",
    bar: "bg-rose-500",
  },
  {
    bg: "bg-sky-600",
    text: "text-sky-100",
    border: "border-sky-400",
    bar: "bg-sky-500",
  },
  {
    bg: "bg-amber-500",
    text: "text-slate-950",
    border: "border-amber-300",
    bar: "bg-amber-400",
  },
  {
    bg: "bg-emerald-600",
    text: "text-emerald-100",
    border: "border-emerald-400",
    bar: "bg-emerald-500",
  },
];

export const HostGamePage: React.FC = () => {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();
  const [isFullscreen, setIsFullscreen] = useState(false);

  const {
    state,
    hostStartGame,
    hostNextQuestion,
    hostEndGame,
    hostKickPlayer,
  } = useGameSocket(gameId || "");

  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
      setIsFullscreen(true);
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      }
      setIsFullscreen(false);
    }
  };

  const joinUrl = `${window.location.origin}/join?pin=${state.pin}`;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col justify-between selection:bg-indigo-500 selection:text-white">
      {/* 1. Header Host */}
      <header className="px-6 py-4 bg-slate-900/90 backdrop-blur border-b border-slate-800 flex items-center justify-between sticky top-0 z-20">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3">
            <span className="w-3 h-3 rounded-full bg-emerald-500 animate-ping" />
            <span className="text-xl font-black text-white tracking-wider">
              MÃ PIN: <span className="text-indigo-400 font-mono text-3xl font-black ml-1">{state.pin || "..."}</span>
            </span>
          </div>

          <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-xl bg-slate-800/80 border border-slate-700 text-sm font-semibold text-slate-300">
            <Users className="w-4 h-4 text-indigo-400" />
            <span>{state.playerCount} người chơi</span>
          </div>
        </div>

        {/* Nút điều khiển trận đấu */}
        <div className="flex items-center gap-3">
          {state.status === "lobby" && (
            <button
              onClick={hostStartGame}
              disabled={state.playerCount === 0}
              className="px-6 py-2.5 rounded-2xl bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 active:scale-95 text-white font-bold text-sm shadow-lg shadow-emerald-900/30 flex items-center gap-2 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Play className="w-4 h-4 fill-white" />
              Bắt Đầu Trận Đấu
            </button>
          )}

          {state.status === "question_result" && state.qIndex < state.totalQuestions && (
            <button
              onClick={() => hostNextQuestion(state.qIndex + 1)}
              className="px-6 py-2.5 rounded-2xl bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 active:scale-95 text-white font-bold text-sm shadow-lg shadow-indigo-900/30 flex items-center gap-2 cursor-pointer"
            >
              <SkipForward className="w-4 h-4" />
              Câu Tiếp Theo
            </button>
          )}

          {state.status === "question_result" && state.qIndex >= state.totalQuestions && (
            <button
              onClick={hostEndGame}
              className="px-6 py-2.5 rounded-2xl bg-gradient-to-r from-amber-600 to-amber-500 hover:from-amber-500 hover:to-amber-400 active:scale-95 text-white font-bold text-sm shadow-lg shadow-amber-900/30 flex items-center gap-2 cursor-pointer"
            >
              <Trophy className="w-4 h-4 fill-white" />
              Xem Xếp Hạng & Bế Mạc
            </button>
          )}

          {state.status !== "lobby" && state.status !== "finished" && (
            <button
              onClick={hostEndGame}
              className="p-2.5 rounded-2xl bg-slate-800 hover:bg-rose-600/20 hover:text-rose-400 border border-slate-700 text-slate-400 transition-colors cursor-pointer"
              title="Kết thúc trận sớm"
            >
              <StopCircle className="w-5 h-5" />
            </button>
          )}

          <button
            onClick={toggleFullscreen}
            className="p-2.5 rounded-2xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition-colors cursor-pointer"
            title="Toàn màn hình"
          >
            {isFullscreen ? <Minimize2 className="w-5 h-5" /> : <Maximize2 className="w-5 h-5" />}
          </button>
        </div>
      </header>

      {/* 2. Nội dung chính màn hình Host */}
      <main className="flex-1 flex flex-col justify-center items-center p-6 sm:p-12 max-w-7xl mx-auto w-full">
        {/* LOBBY: QR Code + Danh sách người chơi */}
        {state.status === "lobby" && (
          <div className="w-full grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
            {/* Cột trái: QR Code + Hướng dẫn vào phòng */}
            <div className="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-3xl p-8 flex flex-col items-center text-center shadow-2xl">
              <div className="bg-white p-4 rounded-2xl shadow-xl mb-4">
                <QRCodeSVG value={joinUrl} size={220} level="M" />
              </div>
              <h2 className="text-xl font-black text-white">Quét QR hoặc truy cập</h2>
              <p className="text-indigo-400 font-mono text-sm mt-1 mb-4 select-all">
                {window.location.origin}/join
              </p>
              <div className="p-3 rounded-2xl bg-slate-800/80 border border-slate-700 text-slate-300 text-xs font-semibold">
                Nhập PIN: <span className="font-mono text-indigo-300 font-bold text-lg">{state.pin}</span>
              </div>
            </div>

            {/* Cột phải: Danh sách người chơi đã vào */}
            <div className="lg:col-span-7 bg-slate-900/60 border border-slate-800 rounded-3xl p-8 flex flex-col h-[420px]">
              <div className="flex items-center justify-between pb-4 border-b border-slate-800 mb-4">
                <h3 className="text-lg font-bold text-white flex items-center gap-2">
                  <Users className="w-5 h-5 text-indigo-400" />
                  Đấu Thủ Đã Tham Gia ({state.playerCount})
                </h3>
                <span className="text-xs text-slate-500">Chạm để kick người chơi</span>
              </div>

              <div className="flex-1 overflow-y-auto pr-2 flex flex-wrap content-start gap-3">
                {state.players.length === 0 ? (
                  <div className="w-full h-full flex flex-col items-center justify-center text-slate-500 text-sm">
                    <Sparkles className="w-8 h-8 mb-2 text-slate-600 animate-pulse" />
                    Đang đợi người chơi quét mã...
                  </div>
                ) : (
                  state.players.map((p) => (
                    <div
                      key={p.id}
                      onClick={() => {
                        if (window.confirm(`Bạn có chắc muốn kick "${p.nickname}"? (Cấm vào lại 10 phút)`)) {
                          hostKickPlayer(p.id);
                        }
                      }}
                      className="group flex items-center gap-2 px-4 py-2.5 rounded-2xl bg-slate-800 hover:bg-rose-500/20 border border-slate-700 hover:border-rose-500/40 text-slate-200 hover:text-rose-300 transition-all cursor-pointer font-bold text-sm shadow-sm"
                    >
                      <span>{p.nickname}</span>
                      <UserX className="w-3.5 h-3.5 text-slate-500 group-hover:text-rose-400 transition-colors" />
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {/* ĐANG BẮT ĐẦU */}
        {state.status === "starting" && (
          <div className="text-center">
            <div className="text-9xl font-black text-indigo-400 mb-6 animate-pulse">
              {state.remainingSeconds}
            </div>
            <h2 className="text-3xl font-black text-white uppercase tracking-widest">
              Trận Đấu Bắt Đầu!
            </h2>
          </div>
        )}

        {/* CÂU HỎI ĐANG ACTIVE */}
        {state.status === "question_active" && state.currentQuestion && (
          <div className="w-full flex flex-col gap-8">
            {/* Thanh thời gian & câu hỏi */}
            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-8 relative overflow-hidden shadow-2xl">
              <div className="flex items-center justify-between mb-4">
                <span className="text-sm font-bold uppercase tracking-wider text-indigo-400">
                  Câu hỏi {state.qIndex} / {state.totalQuestions}
                </span>
                <div className="flex items-center gap-2 font-mono text-2xl font-black text-amber-400">
                  <Clock className="w-6 h-6" />
                  <span>{state.remainingSeconds}s</span>
                </div>
              </div>

              <h1 className="text-2xl sm:text-4xl font-black text-white leading-tight">
                {state.currentQuestion.content}
              </h1>

              {/* Progress bar */}
              <div className="absolute bottom-0 left-0 right-0 h-2 bg-slate-800">
                <div
                  className="h-full bg-gradient-to-r from-indigo-500 to-indigo-400 transition-all duration-75"
                  style={{ width: `${state.progressRatio * 100}%` }}
                />
              </div>
            </div>

            {/* 4 Lựa chọn hiển thị cho cả lớp nhìn */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {state.currentQuestion.options.map((opt, idx) => {
                const color = COLOR_CLASSES[idx % COLOR_CLASSES.length];
                return (
                  <div
                    key={opt.id}
                    className={`rounded-3xl p-6 flex items-center gap-4 border shadow-lg ${color.bg} ${color.border} ${color.text}`}
                  >
                    <span className="w-10 h-10 rounded-2xl bg-black/20 flex items-center justify-center font-black text-xl shrink-0">
                      {String.fromCharCode(65 + idx)}
                    </span>
                    <span className="text-xl sm:text-2xl font-bold leading-snug">
                      {opt.content}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* KẾT QUẢ CÂU HỎI: Biểu đồ phân bổ 4 đáp án */}
        {state.status === "question_result" && state.currentQuestion && (
          <div className="w-full flex flex-col gap-8">
            <div className="text-center">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                Kết quả câu {state.qIndex}
              </span>
              <h2 className="text-2xl font-bold text-white mt-1">
                {state.currentQuestion.content}
              </h2>
            </div>

            {/* Biểu đồ phân bổ cột */}
            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-8 shadow-2xl">
              <h3 className="text-sm font-bold text-slate-400 uppercase tracking-wider mb-6 flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-indigo-400" />
                Phân Bổ Câu Trả Lời
              </h3>

              <div className="grid grid-cols-4 gap-4 sm:gap-8 h-64 items-end pb-4 border-b border-slate-800">
                {state.currentQuestion.options.map((opt, idx) => {
                  const color = COLOR_CLASSES[idx % COLOR_CLASSES.length];
                  const count = state.questionResult?.distribution[opt.id] ?? 0;
                  const total = Object.values(state.questionResult?.distribution ?? {}).reduce((a, b) => a + b, 0);
                  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
                  const isCorrect = state.questionResult?.correct_option_ids.includes(opt.id);

                  return (
                    <div key={opt.id} className="flex flex-col items-center gap-2 h-full justify-end">
                      <div className="text-sm font-black text-white">{count}</div>
                      <div
                        className={`w-full max-w-[80px] rounded-t-2xl transition-all duration-500 relative ${
                          isCorrect ? color.bar : "bg-slate-800"
                        }`}
                        style={{ height: `${Math.max(12, pct)}%` }}
                      >
                        {isCorrect && (
                          <div className="absolute -top-3 left-1/2 -translate-x-1/2 p-1 rounded-full bg-emerald-500 text-white shadow">
                            <CheckCircle2 className="w-4 h-4" />
                          </div>
                        )}
                      </div>
                      <span className="font-bold text-xs sm:text-sm text-slate-300">
                        {String.fromCharCode(65 + idx)} ({pct}%)
                      </span>
                    </div>
                  );
                })}
              </div>

              {state.questionResult?.explanation && (
                <div className="mt-6 p-4 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-sm">
                  <span className="font-bold">Giải thích: </span>
                  {state.questionResult.explanation}
                </div>
              )}
            </div>
          </div>
        )}

        {/* KẾT THÚC TRẬN: PODIUM TOP 3 */}
        {state.status === "finished" && (
          <div className="w-full flex flex-col items-center">
            <h1 className="text-4xl sm:text-5xl font-black text-white mb-2 flex items-center gap-3">
              <Trophy className="w-12 h-12 text-amber-400" />
              BẢNG VINH DANH
            </h1>
            <p className="text-slate-400 text-sm mb-12">Top các chiến binh xuất sắc nhất</p>

            {/* Bục vinh quang 3 bậc */}
            <div className="flex items-end justify-center gap-4 sm:gap-8 max-w-2xl w-full mb-12">
              {/* Hạng 2 */}
              <div className="flex flex-col items-center flex-1">
                <div className="font-bold text-base text-slate-200 mb-2 truncate max-w-[120px]">
                  {state.podium[1]?.nickname || "--"}
                </div>
                <div className="text-xs text-slate-400 mb-2">
                  {state.podium[1]?.score.toLocaleString()} pts
                </div>
                <div className="w-full h-40 bg-gradient-to-t from-slate-700 to-slate-500 rounded-t-3xl flex flex-col items-center justify-center text-white shadow-xl">
                  <Medal className="w-8 h-8 text-slate-300 mb-1" />
                  <span className="text-3xl font-black">2</span>
                </div>
              </div>

              {/* Hạng 1 */}
              <div className="flex flex-col items-center flex-1">
                <Crown className="w-10 h-10 text-amber-400 mb-1 animate-bounce" />
                <div className="font-black text-xl text-amber-300 mb-2 truncate max-w-[140px]">
                  {state.podium[0]?.nickname || "--"}
                </div>
                <div className="text-sm font-bold text-amber-400 mb-2">
                  {state.podium[0]?.score.toLocaleString()} pts
                </div>
                <div className="w-full h-56 bg-gradient-to-t from-amber-600 to-amber-400 rounded-t-3xl flex flex-col items-center justify-center text-white shadow-2xl">
                  <Trophy className="w-10 h-10 text-amber-100 mb-1" />
                  <span className="text-4xl font-black">1</span>
                </div>
              </div>

              {/* Hạng 3 */}
              <div className="flex flex-col items-center flex-1">
                <div className="font-bold text-base text-slate-200 mb-2 truncate max-w-[120px]">
                  {state.podium[2]?.nickname || "--"}
                </div>
                <div className="text-xs text-slate-400 mb-2">
                  {state.podium[2]?.score.toLocaleString()} pts
                </div>
                <div className="w-full h-32 bg-gradient-to-t from-amber-800 to-amber-700 rounded-t-3xl flex flex-col items-center justify-center text-white shadow-lg">
                  <Medal className="w-7 h-7 text-amber-400 mb-1" />
                  <span className="text-2xl font-black">3</span>
                </div>
              </div>
            </div>

            {/* Các nút hành động sau trận */}
            <div className="flex items-center gap-4">
              <button
                onClick={() => navigate(`/quizzes`)}
                className="px-6 py-3.5 rounded-2xl bg-slate-800 hover:bg-slate-700 text-white font-bold text-sm cursor-pointer"
              >
                Về Quản Lý Quiz
              </button>
            </div>
          </div>
        )}
      </main>

      {/* Footer nhỏ */}
      <footer className="px-6 py-3 bg-slate-900/60 border-t border-slate-800 text-center text-xs text-slate-500">
        QuizArena Live Engine · WebSocket v1 · Redis Pub/Sub Broadcast
      </footer>
    </div>
  );
};
