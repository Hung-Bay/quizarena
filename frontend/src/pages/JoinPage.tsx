import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { apiRequest } from '../services/apiClient';
import { tokenStore } from '../services/tokenStore';
import { Gamepad2, ArrowRight, AlertCircle, Loader2 } from 'lucide-react';
import type { components } from '../types/api';

const joinSchema = z.object({
  pin: z.string().length(6, 'Mã PIN gồm chính xác 6 chữ số').regex(/^\d+$/, 'Mã PIN chỉ chứa chữ số'),
  nickname: z.string().min(1, 'Vui lòng nhập biệt danh').max(30, 'Biệt danh tối đa 30 ký tự'),
});

export const JoinPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [pin, setPin] = useState(searchParams.get('pin') || '');
  const [nickname, setNickname] = useState('');
  const [errors, setErrors] = useState<{ pin?: string; nickname?: string; general?: string }>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    const pinParam = searchParams.get('pin');
    if (pinParam) {
      setPin(pinParam);
    }
  }, [searchParams]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrors({});

    const validation = joinSchema.safeParse({ pin: pin.trim(), nickname: nickname.trim() });
    if (!validation.success) {
      const fieldErrors: { pin?: string; nickname?: string } = {};
      validation.error.errors.forEach((err) => {
        if (err.path[0] === 'pin') fieldErrors.pin = err.message;
        if (err.path[0] === 'nickname') fieldErrors.nickname = err.message;
      });
      setErrors(fieldErrors);
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await apiRequest<components['schemas']['JoinGameResponse']>('/api/v1/games/join', {
        method: 'POST',
        body: JSON.stringify({
          pin: pin.trim(),
          nickname: nickname.trim(),
        }),
      });

      // Lưu Guest Token vào sessionStorage (sống qua F5/reload giữa trận, hủy khi đóng tab)
      tokenStore.setGuestToken(response.token);

      // Chuyển sang màn hình chơi của player
      navigate(`/play/${response.game_id}`, {
        state: {
          playerId: response.player_id,
          nickname: response.nickname,
        },
      });
    } catch (err: any) {
      setErrors({
        general: err?.message || 'Không thể tham gia phòng. Kiểm tra lại mã PIN hoặc phòng đã kết thúc.',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-[100dvh] flex flex-col justify-center items-center p-4 bg-slate-950 text-slate-100">
      <div className="w-full max-w-sm bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 shadow-2xl relative overflow-hidden">
        {/* Glow */}
        <div className="absolute -top-16 -right-16 w-36 h-36 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute -bottom-16 -left-16 w-36 h-36 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />

        <div className="flex flex-col items-center text-center mb-6">
          <div className="w-14 h-14 rounded-2xl bg-indigo-600/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400 mb-3 shadow-inner">
            <Gamepad2 className="w-8 h-8" />
          </div>
          <h1 className="text-2xl font-black text-white tracking-tight">Vào Trận Đấu</h1>
          <p className="text-slate-400 text-xs mt-1">
            Nhập mã PIN hiển thị trên màn hình của Host để bắt đầu.
          </p>
        </div>

        {errors.general && (
          <div className="mb-5 p-3 rounded-xl bg-red-500/10 border border-red-500/30 flex items-start gap-2.5 text-red-400 text-xs">
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{errors.general}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1">
              Mã PIN (6 số)
            </label>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
              placeholder="123456"
              className={`w-full text-center text-2xl font-mono tracking-widest font-bold py-3 px-4 rounded-xl bg-slate-800/80 border text-cyan-300 placeholder-slate-600 focus:outline-none focus:ring-2 transition-all ${
                errors.pin
                  ? 'border-red-500 focus:ring-red-500/30'
                  : 'border-slate-700 focus:border-cyan-500 focus:ring-cyan-500/20'
              }`}
              disabled={isSubmitting}
            />
            {errors.pin && <p className="text-xs text-red-400 mt-1">{errors.pin}</p>}
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1">
              Biệt danh của bạn
            </label>
            <input
              type="text"
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
              placeholder="VD: Siêu Trí Tuệ"
              maxLength={30}
              className={`w-full text-center py-2.5 px-4 rounded-xl bg-slate-800/80 border text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all ${
                errors.nickname
                  ? 'border-red-500 focus:ring-red-500/30'
                  : 'border-slate-700 focus:border-indigo-500 focus:ring-indigo-500/20'
              }`}
              disabled={isSubmitting}
            />
            {errors.nickname && <p className="text-xs text-red-400 mt-1">{errors.nickname}</p>}
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full mt-4 py-3.5 px-4 rounded-xl bg-gradient-to-r from-indigo-600 to-cyan-600 hover:from-indigo-500 hover:to-cyan-500 active:opacity-90 text-white font-bold flex items-center justify-center gap-2 transition-all shadow-lg shadow-indigo-600/25 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                <span>Đang kết nối...</span>
              </>
            ) : (
              <>
                <span>Tham Gia Ngay</span>
                <ArrowRight className="w-5 h-5" />
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
};
