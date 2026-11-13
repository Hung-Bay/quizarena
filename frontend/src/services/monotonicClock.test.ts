import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MonotonicCountdown } from "./monotonicClock";

describe("MonotonicCountdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("đếm ngược chính xác dù đồng hồ thiết bị bị lệch 30 giây", () => {
    // Giả lập server_time = 1000s, deadline = 1020s (thời lượng 20s)
    const serverTime = 1000;
    const deadline = 1020;

    let latestSeconds = 20;
    let latestRatio = 1.0;
    let completed = false;

    const countdown = new MonotonicCountdown(
      deadline,
      serverTime,
      (state) => {
        latestSeconds = state.remainingSeconds;
        latestRatio = state.progressRatio;
      },
      () => {
        completed = true;
      }
    );

    expect(latestSeconds).toBe(20);
    expect(latestRatio).toBe(1.0);

    // Giả lập máy người dùng bị chỉnh giờ chạy lùi hoặc nhảy cóc 30 giây
    vi.setSystemTime(new Date(Date.now() + 30000));

    // Tua nhanh 5 giây theo fake monotonic timer
    vi.advanceTimersByTime(5000);
    expect(latestSeconds).toBe(15);
    expect(latestRatio).toBeCloseTo(0.75, 1);

    // Tua nhanh thêm 15 giây nữa -> hoàn thành
    vi.advanceTimersByTime(15000);
    expect(latestSeconds).toBe(0);
    expect(latestRatio).toBe(0);
    expect(completed).toBe(true);

    countdown.stop();
  });

  it("dừng kịp thời khi gọi stop()", () => {
    let tickCount = 0;
    const countdown = new MonotonicCountdown(
      100,
      80,
      () => {
        tickCount++;
      }
    );

    countdown.stop();
    const countAfterStop = tickCount;
    vi.advanceTimersByTime(2000);
    expect(tickCount).toBe(countAfterStop);
  });
});
