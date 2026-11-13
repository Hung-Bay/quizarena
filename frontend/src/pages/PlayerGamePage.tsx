import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useGameSocket } from "../hooks/useGameSocket";
import {
  Triangle,
  Diamond,
  Circle,
  Square,
  Flame,
  Trophy,
  CheckCircle2,
  XCircle,
  Loader2,
} from "lucide-react";

const OPTION_STYLES = [
  {
    bg: "bg-rose-600 active:bg-rose-700 hover:bg-rose-500 text-white shadow-rose-900/40",
    border: "border-rose-400/40",
    icon: Triangle,
    label: "Đỏ",
  },
  {
    bg: "bg-sky-600 active:bg-sky-700 hover:bg-sky-500 text-white shadow-sky-900/40",
    border: "border-sky-400/40",
    icon: Diamond,
    label: "Xanh dương",
  },
  {
    bg: "bg-amber-500 active:bg-amber-600 hover:bg-amber-400 text-slate-950 shadow-amber-900/40",
    border: "border-amber-300/40",
    icon: Circle,
    label: "Vàng",
  },
  {
    bg: "bg-emerald-600 active:bg-emerald-700 hover:bg-emerald-500 text-white shadow-emerald-900/40",
    border: "border-emerald-400/40",
    icon: Square,
    label: "Xanh lá",
  },
];

export const PlayerGamePage: React.FC = () => {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();

  const { state, playerSubmitAnswer } = useGameSocket(gameId || "");

  // Bị kick khỏi phòng
  if (state.connectionStatus === "kicked") {
    return (
      <div className="min-h-[100dvh] bg-slate-950 text-slate-100 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-16 h-16 rounded-full bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400 mb-4">
          <XCircle className="w-10 h-10" />
        </div>
        <h1 className="text-2xl font-black text-white mb-2">Đã Bị Loại</h1>
        <p className="text-slate-400 max-w-xs mb-6 text-sm">
          {state.errorMessage || "Bạn đã bị Host loại khỏi phòng đấu này."}
        </p>
        <button
          onClick={() => navigate("/join")}
          className="px-6 py-3 rounded-2xl bg-slate-800 hover:bg-slate-700 text-white font-bold text-sm"
        >
          Trở về màn hình tham gia
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] bg-slate-950 text-slate-100 flex flex-col justify-between select-none touch-manipulation overflow-hidden">
      {/* 1. Header cố định */}
      <header className="px-4 py-3 bg-slate-900/80 backdrop-blur border-b border-slate-800 flex items-center justify-between z-10">
        <div className="flex items-center gap-3">
          <span className="text-xs font-mono font-bold px-2.5 py-1 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            PIN: {state.pin || "..."}
          </span>
          {state.connectionStatus === "reconnecting" && (
            <span className="text-xs text-amber-400 flex items-center gap-1.5 animate-pulse">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Đang nối lại...
            </span>
          )}
        </div>

        <div className="flex items-center gap-4">
          {/* Điểm & Streak */}
          <div className="flex items-center gap-1.5 text-xs font-bold text-amber-400 bg-amber-400/10 px-2.5 py-1 rounded-lg border border-amber-400/20">
            <Flame className="w-4 h-4 fill-amber-400" />
            <span>{state.personalStreak}</span>
          </div>

          <div className="text-sm font-black text-white bg-slate-800 px-3 py-1 rounded-lg">
            {state.personalScore.toLocaleString()} pts
          </div>
        </div>
      </header>

      {/* 2. Thanh tiến trình thời gian Monotonic */}
      {state.status === "question_active" && (
        <div className="w-full bg-slate-900 h-2 relative overflow-hidden">
          <div
            className={`h-full transition-all duration-75 ${
              state.remainingSeconds <= 5 ? "bg-rose-500" : "bg-indigo-500"
            }`}
            style={{ width: `${state.progressRatio * 100}%` }}
          />
        </div>
      )}

      {/* 3. Nội dung chính theo trạng thái trận */}
      <main className="flex-1 flex flex-col justify-center items-center p-4">
        {/* TRẠNG THÁI LOBBY */}
        {state.status === "lobby" && (
          <div className="text-center py-12 px-6 max-w-sm">
            <div className="w-20 h-20 mx-auto rounded-3xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400 mb-6 shadow-inner animate-pulse">
              <Loader2 className="w-10 h-10 animate-spin" />
            </div>
            <h2 className="text-2xl font-black text-white mb-2">Đã Vào Phòng Đấu!</h2>
            <p className="text-slate-400 text-sm mb-6">
              Hãy chú ý màn hình máy chiếu của Host. Trận đấu sắp bắt đầu!
            </p>
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900 border border-slate-800 text-xs text-slate-300">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
              <span>Đang kết nối WebSocket trực tiếp</span>
            </div>
          </div>
        )}

        {/* TRẠNG THÁI BẮT ĐẦU */}
        {state.status === "starting" && (
          <div className="text-center animate-bounce">
            <div className="text-7xl font-black text-indigo-400 mb-4">
              {state.remainingSeconds}
            </div>
            <h2 className="text-xl font-black text-white uppercase tracking-wider">
              Chuẩn Bị Sẵn Sàng!
            </h2>
          </div>
        )}

        {/* TRẠNG THÁI CÂU HỎI ĐANG CHẠY */}
        {state.status === "question_active" && state.currentQuestion && (
          <div className="w-full h-full flex flex-col justify-between py-2">
            {/* Header câu hỏi */}
            <div className="text-center mb-4">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                Câu {state.qIndex} / {state.totalQuestions}
              </span>
              <h2 className="text-lg sm:text-xl font-bold text-white mt-1 line-clamp-3">
                {state.currentQuestion.content}
              </h2>
            </div>

            {/* 4 Nút màu cảm ứng lớn cho điện thoại */}
            <div className="grid grid-cols-2 gap-3 sm:gap-4 flex-1 min-h-[50vh]">
              {state.currentQuestion.options.map((opt, idx) => {
                const style = OPTION_STYLES[idx % OPTION_STYLES.length];
                const Icon = style.icon;
                const isSelected = state.selectedOptionId === opt.id;
                const isDisabled = state.isAnswerSubmitted;

                return (
                  <button
                    key={opt.id}
                    disabled={isDisabled}
                    onClick={() => playerSubmitAnswer(opt.id)}
                    className={`relative rounded-3xl p-4 flex flex-col items-center justify-center gap-2 text-center transition-all shadow-lg active:scale-95 cursor-pointer border ${
                      style.bg
                    } ${style.border} ${
                      isSelected
                        ? "ring-4 ring-white scale-[1.02] z-10 brightness-110"
                        : isDisabled
                        ? "opacity-40 grayscale-[30%]"
                        : ""
                    }`}
                  >
                    <Icon className="w-10 h-10 sm:w-12 sm:h-12 drop-shadow" />
                    <span className="text-sm sm:text-base font-bold line-clamp-3">
                      {opt.content}
                    </span>
                    {isSelected && (
                      <span className="absolute top-3 right-3 p-1 rounded-full bg-white text-slate-950 shadow">
                        <CheckCircle2 className="w-4 h-4" />
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            {/* Thông báo sau khi nộp */}
            {state.isAnswerSubmitted && (
              <div className="mt-4 p-3 rounded-2xl bg-indigo-600/20 border border-indigo-500/30 text-indigo-300 text-center text-xs font-bold animate-pulse">
                ✓ Đã ghi nhận câu trả lời! Đang chờ đối thủ nộp bài...
              </div>
            )}
          </div>
        )}

        {/* TRẠNG THÁI KẾT QUẢ CÂU HỎI */}
        {state.status === "question_result" && (
          <div className="text-center max-w-sm w-full py-8">
            {state.questionResult?.personal?.is_correct ? (
              <div className="p-6 rounded-3xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 mb-6">
                <CheckCircle2 className="w-16 h-16 mx-auto mb-3" />
                <h2 className="text-2xl font-black text-white mb-1">CHÍNH XÁC! 🎉</h2>
                <p className="text-emerald-300 font-bold text-lg">
                  +{state.questionResult.personal.added_score} điểm
                </p>
              </div>
            ) : (
              <div className="p-6 rounded-3xl bg-rose-500/10 border border-rose-500/30 text-rose-400 mb-6">
                <XCircle className="w-16 h-16 mx-auto mb-3" />
                <h2 className="text-2xl font-black text-white mb-1">CHƯA ĐÚNG 😢</h2>
                <p className="text-rose-300 text-sm">Cố gắng ở câu kế tiếp nhé!</p>
              </div>
            )}

            {/* Thứ hạng hiện tại */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 flex items-center justify-around">
              <div>
                <span className="text-xs text-slate-400 block">Thứ hạng</span>
                <span className="text-2xl font-black text-amber-400">
                  #{state.personalRank || "--"}
                </span>
              </div>
              <div className="h-8 w-px bg-slate-800" />
              <div>
                <span className="text-xs text-slate-400 block">Chuỗi đúng</span>
                <span className="text-2xl font-black text-rose-400 flex items-center justify-center gap-1">
                  <Flame className="w-5 h-5 fill-rose-400" />
                  {state.personalStreak}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* TRẠNG THÁI KẾT THÚC TRẬN */}
        {state.status === "finished" && (
          <div className="text-center max-w-sm w-full py-8">
            <div className="w-20 h-20 mx-auto rounded-3xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400 mb-4 shadow-inner">
              <Trophy className="w-10 h-10" />
            </div>
            <h2 className="text-3xl font-black text-white mb-1">Trận Đấu Kết Thúc</h2>
            <p className="text-slate-400 text-sm mb-6">Bạn đã hoàn thành xuất sắc bài thi!</p>

            <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 mb-6">
              <span className="text-xs uppercase tracking-wider text-slate-400 font-bold block mb-1">
                Thứ Hạng Chung Cuộc
              </span>
              <div className="text-5xl font-black text-amber-400 mb-2">
                #{state.personalRank || 1}
              </div>
              <div className="text-lg font-bold text-slate-200">
                Tổng điểm: {state.personalScore.toLocaleString()} pts
              </div>
            </div>

            <button
              onClick={() => navigate("/join")}
              className="w-full py-4 rounded-2xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-base shadow-lg shadow-indigo-600/30 cursor-pointer"
            >
              Chơi Trận Khác
            </button>
          </div>
        )}
      </main>

      {/* Footer nhỏ */}
      <footer className="px-4 py-2 bg-slate-950 border-t border-slate-900 text-center text-[10px] text-slate-600">
        QuizArena Live Engine · Mobile Optimized (100dvh)
      </footer>
    </div>
  );
};
