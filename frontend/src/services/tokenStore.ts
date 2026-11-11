/**
 * Quản lý Token theo kiến trúc bảo mật:
 * 1. Access Token của Host/User: CHỈ LƯU TRONG BỘ NHỚ (in-memory variable),
 *    tuyệt đối không ghi vào localStorage/sessionStorage/cookies để chống XSS đánh cắp (FR-AUTH-04).
 * 2. Guest Token của người chơi ẩn danh: Lưu trong sessionStorage (sống qua F5 reload giữa trận,
 *    tự hủy khi đóng tab) (FR-AUTH-05, ADR-001).
 * 3. Refresh Token: Được quản lý độc quyền qua HttpOnly Cookie bởi Backend.
 */

type TokenListener = (token: string | null) => void;

let inMemoryAccessToken: string | null = null;
const listeners = new Set<TokenListener>();

export const tokenStore = {
  getAccessToken(): string | null {
    return inMemoryAccessToken;
  },

  setAccessToken(token: string | null): void {
    inMemoryAccessToken = token;
    listeners.forEach((listener) => listener(token));
  },

  clearAccessToken(): void {
    this.setAccessToken(null);
  },

  isAuthenticated(): boolean {
    return inMemoryAccessToken !== null;
  },

  subscribe(listener: TokenListener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },

  // --- Guest Token (sessionStorage) ---
  getGuestToken(): string | null {
    try {
      return sessionStorage.getItem('quizarena_guest_token');
    } catch {
      return null;
    }
  },

  setGuestToken(token: string | null): void {
    try {
      if (token) {
        sessionStorage.setItem('quizarena_guest_token', token);
      } else {
        sessionStorage.removeItem('quizarena_guest_token');
      }
    } catch {
      // sessionStorage unavailable or quota exceeded
    }
  },

  clearGuestToken(): void {
    this.setGuestToken(null);
  },
};
