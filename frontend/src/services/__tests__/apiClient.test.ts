import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiRequest } from '../apiClient';
import { tokenStore } from '../tokenStore';

describe('Auth Module & Single-Flight Refresh Interceptor', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    tokenStore.clearAccessToken();
  });

  it('10 concurrent requests receiving 401 trigger /api/v1/auth/refresh exactly ONCE and resolve successfully', async () => {
    let refreshCalls = 0;
    tokenStore.setAccessToken('expired-token');

    // Giả lập backend:
    // - Endpoint /api/v1/auth/refresh cấp token mới "fresh-token-xyz"
    // - Endpoint /api/v1/resource yêu cầu Bearer fresh-token-xyz, nếu là expired-token thì trả 401
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const endpoint = url.toString();
      const authHeader = (init?.headers as Record<string, string>)?.[
        'Authorization'
      ];

      if (endpoint.includes('/api/v1/auth/refresh')) {
        refreshCalls++;
        // Thêm độ trễ giả lập mạng để các request đồng thời khác kịp ùa vào
        await new Promise((resolve) => setTimeout(resolve, 50));
        return {
          ok: true,
          status: 200,
          json: async () => ({
            access_token: 'fresh-token-xyz',
            token_type: 'bearer',
            expires_in: 900,
          }),
        };
      }

      if (endpoint.includes('/api/v1/resource')) {
        if (authHeader === 'Bearer fresh-token-xyz') {
          return {
            ok: true,
            status: 200,
            json: async () => ({ status: 'ok', requestId: endpoint }),
          };
        }
        return {
          ok: false,
          status: 401,
          json: async () => ({ error: { code: 'UNAUTHORIZED', message: 'Token expired' } }),
        };
      }

      return {
        ok: false,
        status: 404,
        json: async () => ({ error: 'Not Found' }),
      };
    }));

    // Gửi đồng thời 10 request protected API
    const requests = Array.from({ length: 10 }, (_, i) =>
      apiRequest<{ status: string }>(`/api/v1/resource?id=${i}`)
    );

    const results = await Promise.all(requests);

    // 1. Toàn bộ 10 request đều hoàn thành thành công sau khi token được làm mới
    expect(results).toHaveLength(10);
    results.forEach((res) => {
      expect(res.status).toBe('ok');
    });

    // 2. Tiêu chí cốt lõi (Monday DoD): Endpoint refresh CHỈ ĐƯỢC GỌI ĐÚNG 1 LẦN
    expect(refreshCalls).toBe(1);

    // 3. Token mới đã được cập nhật an toàn vào bộ nhớ
    expect(tokenStore.getAccessToken()).toBe('fresh-token-xyz');
  });

  it('10 concurrent requests fail cleanly when refresh fails and token is cleared', async () => {
    let refreshCalls = 0;
    tokenStore.setAccessToken('expired-token');

    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      const endpoint = url.toString();

      if (endpoint.includes('/api/v1/auth/refresh')) {
        refreshCalls++;
        await new Promise((resolve) => setTimeout(resolve, 30));
        return {
          ok: false,
          status: 401,
          json: async () => ({ error: { message: 'Refresh token expired' } }),
        };
      }

      return {
        ok: false,
        status: 401,
        json: async () => ({ error: { message: 'Unauthorized' } }),
      };
    }));

    const requests = Array.from({ length: 10 }, (_, i) =>
      apiRequest(`/api/v1/resource?id=${i}`)
    );

    const outcomes = await Promise.allSettled(requests);

    // Cả 10 request đều bị từ chối với status 401
    expect(outcomes).toHaveLength(10);
    outcomes.forEach((outcome) => {
      expect(outcome.status).toBe('rejected');
    });

    // Refresh vẫn chỉ được gọi đúng 1 lần
    expect(refreshCalls).toBe(1);

    // Token đã bị xóa khỏi bộ nhớ
    expect(tokenStore.getAccessToken()).toBeNull();
  });
});
