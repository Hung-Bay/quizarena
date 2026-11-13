/**
 * Mô-đun đếm ngược đồng hồ đơn điệu (Monotonic Clock Countdown).
 *
 * Yêu cầu kỹ thuật:
 * - Đếm ngược hoàn toàn độc lập với đồng hồ hệ thống của máy khách (Client OS clock).
 * - Khi nhận `question.start`:
 *     còn_lại = deadline - server_time
 * - Đếm xuống bằng `performance.now()` (đồng hồ đơn điệu, không bị giật lùi hay nhảy cóc khi máy khách chỉnh giờ).
 * - Máy lệch 30 giây hoặc sai múi giờ vẫn đếm đúng từng mili-giây.
 */

export interface CountdownState {
  remainingSeconds: number;
  remainingMs: number;
  progressRatio: number; // 1.0 (bắt đầu) -> 0.0 (hết giờ)
  isFinished: boolean;
}

export type CountdownCallback = (state: CountdownState) => void;

export class MonotonicCountdown {
  private totalDurationMs: number;
  private startMonotonic: number;
  private timerId: number | null = null;
  private onTick: CountdownCallback;
  private onComplete?: () => void;
  private isStopped = false;

  constructor(
    deadline: number,
    serverTime: number,
    onTick: CountdownCallback,
    onComplete?: () => void
  ) {
    // Nếu deadline/serverTime ở dạng giây (epoch < 1e11), quy đổi sang mili-giây
    const dMs = deadline < 1e11 ? deadline * 1000 : deadline;
    const sMs = serverTime < 1e11 ? serverTime * 1000 : serverTime;

    this.totalDurationMs = Math.max(0, dMs - sMs);
    this.startMonotonic = performance.now();
    this.onTick = onTick;
    this.onComplete = onComplete;

    this.start();
  }

  private start(): void {
    const tick = () => {
      if (this.isStopped) return;

      const elapsed = performance.now() - this.startMonotonic;
      const remainingMs = Math.max(0, this.totalDurationMs - elapsed);
      const remainingSeconds = Math.ceil(remainingMs / 1000);
      const progressRatio =
        this.totalDurationMs > 0
          ? Math.max(0, Math.min(1, remainingMs / this.totalDurationMs))
          : 0;
      const isFinished = remainingMs <= 0;

      this.onTick({
        remainingSeconds,
        remainingMs,
        progressRatio,
        isFinished,
      });

      if (isFinished) {
        this.stop();
        if (this.onComplete) {
          this.onComplete();
        }
      } else {
        // Cập nhật mỗi 50ms để thanh tiến trình chạy mượt mà 60fps
        this.timerId = window.setTimeout(tick, 50);
      }
    };

    // Chạy tick đầu tiên ngay lập tức
    tick();
  }

  public stop(): void {
    this.isStopped = true;
    if (this.timerId !== null) {
      clearTimeout(this.timerId);
      this.timerId = null;
    }
  }

  public getRemainingMs(): number {
    const elapsed = performance.now() - this.startMonotonic;
    return Math.max(0, this.totalDurationMs - elapsed);
  }
}
