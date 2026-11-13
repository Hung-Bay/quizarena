import { tokenStore } from './tokenStore';

export class ApiError extends Error {
  status: number;
  data: any;

  constructor(status: number, message: string, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

interface RequestOptions extends RequestInit {
  skipAuth?: boolean;
  _isRetry?: boolean;
}

// Biến singleton giữ Promise của lượt gọi refresh đang chạy (Single-flight)
let activeRefreshPromise: Promise<string | null> | null = null;

/**
 * Thực hiện làm mới Access Token bằng HttpOnly Cookie (POST /api/v1/auth/refresh).
 * Đảm bảo cơ chế Single-Flight: Nếu có N request đồng thời phát hiện token hết hạn,
 * chỉ duy nhất 1 request mạng gọi endpoint refresh, N-1 request còn lại chờ chung Promise này.
 */
export async function refreshAccessToken(): Promise<string | null> {
  if (activeRefreshPromise) {
    return activeRefreshPromise;
  }

  activeRefreshPromise = (async () => {
    try {
      const response = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include', // Mang theo HttpOnly cookie refresh_token
      });

      if (!response.ok) {
        tokenStore.clearAccessToken();
        return null;
      }

      const data = await response.json();
      const newToken = data.access_token;
      if (newToken) {
        tokenStore.setAccessToken(newToken);
        return newToken;
      }

      tokenStore.clearAccessToken();
      return null;
    } catch {
      tokenStore.clearAccessToken();
      return null;
    } finally {
      activeRefreshPromise = null;
    }
  })();

  return activeRefreshPromise;
}

/**
 * Silent Refresh khi ứng dụng vừa tải (App Mount).
 * Tự động hồi phục phiên đăng nhập từ Refresh Token trong Cookie mà không yêu cầu user nhập lại.
 */
export async function silentRefresh(): Promise<boolean> {
  const token = await refreshAccessToken();
  return token !== null;
}

/**
 * Wrapper HTTP Client chính cho ứng dụng.
 * Tự động gắn Bearer Token từ tokenStore và tự phục hồi khi gặp lỗi 401 bằng Single-Flight Interceptor.
 */
export async function apiRequest<T = any>(
  endpoint: string,
  options: RequestOptions = {}
): Promise<T> {
  const { skipAuth = false, _isRetry = false, headers = {}, ...customConfig } = options;

  const reqHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(headers as Record<string, string>),
  };

  if (!skipAuth) {
    const token = tokenStore.getAccessToken() || tokenStore.getGuestToken();
    if (token) {
      reqHeaders['Authorization'] = `Bearer ${token}`;
    }
  }

  const response = await fetch(endpoint, {
    ...customConfig,
    headers: reqHeaders,
    credentials: 'include',
  });

  // Xử lý 401 Unauthorized với Single-flight Refresh
  if (response.status === 401 && !endpoint.includes('/auth/refresh') && !endpoint.includes('/auth/login') && !_isRetry) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      // Replay request ban đầu với Access Token mới
      return apiRequest<T>(endpoint, {
        ...options,
        _isRetry: true,
      });
    }

    // Refresh thất bại -> Quăng lỗi 401
    tokenStore.clearAccessToken();
    let errorData = null;
    try {
      errorData = await response.json();
    } catch {
      // ignore json parse error
    }
    throw new ApiError(401, errorData?.error?.message || 'Phiên đăng nhập đã hết hạn', errorData);
  }

  if (!response.ok) {
    let errorData = null;
    try {
      errorData = await response.json();
    } catch {
      // ignore
    }
    const message = errorData?.error?.message || errorData?.detail || `HTTP ${response.status}: ${response.statusText}`;
    throw new ApiError(response.status, message, errorData);
  }

  // Đối với 204 No Content
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}
