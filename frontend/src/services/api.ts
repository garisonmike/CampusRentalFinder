// src/services/api.ts

import type {
  AuthTokens,
  LoginCredentials,
  RegisterData,
  Rental,
  Review,
  Statistics,
  User
} from '@/types';
import axios from 'axios';

// Base URL must match Django backend
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor: add JWT access token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor: refresh token if expired
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken) {
        try {
          const { data } = await axios.post(`${API_BASE_URL}/auth/token/refresh/`, {
            refresh: refreshToken,
          });

          localStorage.setItem('access_token', data.access);
          originalRequest.headers.Authorization = `Bearer ${data.access}`;

          return api(originalRequest);
        } catch (refreshError) {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.href = '/login';
          return Promise.reject(refreshError);
        }
      }
    }

    return Promise.reject(error);
  }
);

//
// AUTH API
//
export const authApi = {
  register: async (data: RegisterData) => {
    const response = await api.post<{ user: User; tokens: AuthTokens }>('/auth/register/', data);
    return response.data;
  },

  login: async (credentials: LoginCredentials) => {
    const response = await api.post<{ user: User; tokens: AuthTokens }>('/auth/login/', credentials);
    return response.data;
  },

  logout: (data: { refresh: string }) =>
    api.post("/auth/logout/", data),


  refreshToken: async (refreshToken: string) => {
    const response = await api.post<{ access: string }>('/auth/token/refresh/', { refresh: refreshToken });
    return response.data;
  },
};

//
// RENTALS API
//
export const rentalsApi = {
  getAll: async (params?: { search?: string; city?: string; min_price?: number; max_price?: number }) => {
    const response = await api.get<Rental[]>('/rentals/', { params });
    return response.data;
  },

  getById: async (id: string) => {
    const response = await api.get<Rental>(`/rentals/${id}/`);
    return response.data;
  },

  create: async (data: FormData) => {
    const response = await api.post<Rental>('/rentals/', data, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  update: async (id: string, data: FormData) => {
    const response = await api.put<Rental>(`/rentals/${id}/`, data, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  delete: async (id: string) => {
    await api.delete(`/rentals/${id}/`);
  },

  getFeatured: async () => {
    const response = await api.get<Rental[]>('/rentals/featured/');
    return response.data;
  },

  getRecent: async () => {
    const response = await api.get<Rental[]>('/rentals/recent/');
    return response.data;
  },

  getTopRated: async () => {
    const response = await api.get<Rental[]>('/rentals/top-rated/');
    return response.data;
  },
};

//
// REVIEWS API
//
export const reviewsApi = {
  create: async (data: { rental: string; rating: number; comment: string }) => {
    const response = await api.post<Review>('/reviews/', data);
    return response.data;
  },

  getRentalStatistics: async (rentalId: string) => {
    const response = await api.get(`/reviews/statistics/${rentalId}/`);
    return response.data;
  },
};

//
// PROFILE API
//
export const profileApi = {
  get: async () => {
    const response = await api.get<User>('/auth/profile/');
    return response.data;
  },

  update: async (data: Partial<User>) => {
    const response = await api.put<User>('/auth/profile/', data);
    return response.data;
  },
};

//
// ADMIN API
//
export const adminApi = {
  getStatistics: async () => {
    const response = await api.get<Statistics>('/admin/statistics/');
    return response.data;
  },
};

export default api;
