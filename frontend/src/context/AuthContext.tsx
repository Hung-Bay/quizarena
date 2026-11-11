import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { apiRequest, silentRefresh } from '../services/apiClient';
import { tokenStore } from '../services/tokenStore';
import type { components } from '../types/api';

export type User = components['schemas']['UserOut'];

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchCurrentUser = useCallback(async () => {
    try {
      const userData = await apiRequest<User>('/api/v1/auth/me');
      setUser(userData);
      return userData;
    } catch {
      setUser(null);
      tokenStore.clearAccessToken();
      return null;
    }
  }, []);

  // Silent refresh khi ứng dụng vừa tải (App Mount)
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const success = await silentRefresh();
        if (success && mounted) {
          await fetchCurrentUser();
        }
      } catch {
        // Phiên chưa đăng nhập hoặc cookie hết hạn
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      mounted = false;
    };
  }, [fetchCurrentUser]);

  const login = async (email: string, password: string) => {
    const data = await apiRequest<components['schemas']['LoginResponse']>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
      skipAuth: true,
    });

    tokenStore.setAccessToken(data.access_token);
    setUser(data.user);
  };

  const register = async (email: string, password: string, displayName?: string) => {
    await apiRequest<components['schemas']['UserOut']>('/api/v1/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        email,
        password,
        display_name: displayName || undefined,
      }),
      skipAuth: true,
    });

    // Tự động đăng nhập sau khi đăng ký thành công
    await login(email, password);
  };

  const logout = async () => {
    try {
      await apiRequest('/api/v1/auth/logout', {
        method: 'POST',
      });
    } catch {
      // Bỏ qua lỗi mạng khi logout
    } finally {
      tokenStore.clearAccessToken();
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
