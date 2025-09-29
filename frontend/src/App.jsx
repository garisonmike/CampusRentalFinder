import React, { Suspense, useEffect } from 'react'
import { Route, Routes } from 'react-router-dom'

// Layout Components
import Footer from './components/layout/Footer'
import Navbar from './components/layout/Navbar'
import LoadingSpinner from './components/ui/LoadingSpinner'

// Store
import { useAuthStore } from './services/store/authStore'

// Lazy load pages for better performance
const HomePage = React.lazy(() => import('./pages/HomePage'))
const LoginPage = React.lazy(() => import('./pages/auth/LoginPage'))
const RegisterPage = React.lazy(() => import('./pages/auth/RegisterPage'))
const RentalsPage = React.lazy(() => import('./pages/rentals/RentalsPage'))

// Import other components from the same file
import {
  CreateRentalPage,
  DashboardPage,
  FavoritesPage,
  NotFoundPage,
  ProfilePage,
  RentalDetailPage
} from './pages/rentals/RentalsPage'

// Protected Route Component
const ProtectedRoute = ({ children, requiredUserType = null }) => {
  const { user, isAuthenticated } = useAuthStore()

  if (!isAuthenticated) {
    return <LoginPage />
  }

  if (requiredUserType && user?.user_type !== requiredUserType) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-gray-900">Access Denied</h1>
          <p className="text-gray-600 mt-2">
            You don't have permission to access this page.
          </p>
        </div>
      </div>
    )
  }

  return children
}

// Loading Component for Suspense
const PageLoader = () => (
  <div className="min-h-screen flex items-center justify-center">
    <LoadingSpinner size="large" />
  </div>
)

function App() {
  const { checkAuthStatus, isLoading } = useAuthStore()

  // Check authentication status on app load
  useEffect(() => {
    checkAuthStatus()
  }, [checkAuthStatus])

  // Show loading screen while checking auth
  if (isLoading) {
    return <PageLoader />
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Navigation */}
      <Navbar />

      {/* Main Content */}
      <main className="flex-grow">
        <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* Public Routes */}
            <Route path="/" element={<HomePage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/rentals" element={<RentalsPage />} />
            <Route path="/rentals/:id" element={<RentalDetailPage />} />

            {/* Protected Routes */}
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <DashboardPage />
                </ProtectedRoute>
              }
            />

            <Route
              path="/profile"
              element={
                <ProtectedRoute>
                  <ProfilePage />
                </ProtectedRoute>
              }
            />

            <Route
              path="/favorites"
              element={
                <ProtectedRoute requiredUserType="tenant">
                  <FavoritesPage />
                </ProtectedRoute>
              }
            />

            {/* Landlord Only Routes */}
            <Route
              path="/rentals/create"
              element={
                <ProtectedRoute requiredUserType="landlord">
                  <CreateRentalPage />
                </ProtectedRoute>
              }
            />

            <Route
              path="/rentals/:id/edit"
              element={
                <ProtectedRoute requiredUserType="landlord">
                  <CreateRentalPage />
                </ProtectedRoute>
              }
            />

            {/* Admin Routes */}
            <Route
              path="/admin/*"
              element={
                <ProtectedRoute requiredUserType="admin">
                  <div className="p-8">
                    <h1 className="text-2xl font-bold">Admin Panel</h1>
                    <p className="text-gray-600">Admin features coming soon...</p>
                  </div>
                </ProtectedRoute>
              }
            />

            {/* 404 Route */}
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </Suspense>
      </main>

      {/* Footer */}
      <Footer />
    </div>
  )
}

export default App