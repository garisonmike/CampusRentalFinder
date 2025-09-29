import axios from 'axios'
import toast from 'react-hot-toast'

// Create axios instance
const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || '/api/v1',
    timeout: 10000,
})

// Request interceptor - add auth token
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('access_token')
    if (token) {
        config.headers.Authorization = `Bearer ${token}`
    }
    return config
})

// Response interceptor - handle token refresh
api.interceptors.response.use(
    (response) => response,
    async (error) => {
        const originalRequest = error.config

        if (error.response?.status === 401 && !originalRequest._retry) {
            originalRequest._retry = true

            const refreshToken = localStorage.getItem('refresh_token')
            if (refreshToken) {
                try {
                    const response = await axios.post('/api/v1/auth/token/refresh/', {
                        refresh: refreshToken,
                    })

                    localStorage.setItem('access_token', response.data.access)
                    return api(originalRequest)
                } catch (refreshError) {
                    localStorage.removeItem('access_token')
                    localStorage.removeItem('refresh_token')
                    localStorage.removeItem('user')
                    window.location.href = '/login'
                }
            } else {
                window.location.href = '/login'
            }
        }

        // Show error message
        if (error.response?.data?.message) {
            toast.error(error.response.data.message)
        } else if (error.message) {
            toast.error(error.message)
        }

        return Promise.reject(error)
    }
)

// Auth API
export const authAPI = {
    register: (userData) => api.post('/auth/register/', userData),
    login: (credentials) => api.post('/auth/login/', credentials),
    logout: (refreshToken) => api.post('/auth/logout/', { refresh: refreshToken }),
    getCurrentUser: () => api.get('/auth/me/'),
}

// Rentals API
export const rentalsAPI = {
    getAll: (params = {}) => api.get('/rentals/properties/', { params }),
    getById: (id) => api.get(`/rentals/properties/${id}/`),
    create: (data) => api.post('/rentals/properties/', data),
    update: (id, data) => api.patch(`/rentals/properties/${id}/`, data),
    delete: (id) => api.delete(`/rentals/properties/${id}/`),
    toggleFavorite: (id) => api.post(`/rentals/properties/${id}/toggle_favorite/`),
    getFavorites: () => api.get('/rentals/properties/favorites/'),
    getMyProperties: () => api.get('/rentals/properties/my_properties/'),
}

// Reviews API
export const reviewsAPI = {
    getForRental: (rentalId) => api.get(`/reviews/rental/${rentalId}/`),
    create: (data) => api.post('/reviews/', data),
    update: (id, data) => api.patch(`/reviews/${id}/`, data),
    delete: (id) => api.delete(`/reviews/${id}/`),
}

export default api