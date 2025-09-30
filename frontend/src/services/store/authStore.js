// ./services/store/authStore.js
import { create } from 'zustand';

export const useAuthStore = create((set) => ({
    user: null,
    isAuthenticated: false,
    isLoading: false,

    // Set user and update auth state
    setUser: (user) => set({ user, isAuthenticated: !!user }),

    // Check if user exists in localStorage (simulate backend check)
    checkAuthStatus: async () => {
        set({ isLoading: true });
        try {
            const storedUser = localStorage.getItem('user');
            if (storedUser) {
                const parsedUser = JSON.parse(storedUser);
                set({ user: parsedUser, isAuthenticated: true });
            } else {
                set({ user: null, isAuthenticated: false });
            }
        } catch (err) {
            console.error('Auth check failed:', err);
            set({ user: null, isAuthenticated: false });
        } finally {
            set({ isLoading: false });
        }
    },
}));
