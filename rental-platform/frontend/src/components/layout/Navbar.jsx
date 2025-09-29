import { HeartIcon, HomeIcon, PlusIcon, UserIcon } from '@heroicons/react/24/outline'
import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../services/store/authStore'

const Navbar = () => {
    const { user, isAuthenticated, logout } = useAuthStore()
    const navigate = useNavigate()

    const handleLogout = async () => {
        await logout()
        navigate('/')
    }

    return (
        <nav className="bg-white shadow-sm border-b border-gray-200">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                <div className="flex justify-between items-center h-16">
                    {/* Logo */}
                    <Link to="/" className="flex items-center space-x-2">
                        <HomeIcon className="h-8 w-8 text-blue-600" />
                        <span className="text-xl font-bold text-gray-900">
                            Rental Platform
                        </span>
                    </Link>

                    {/* Navigation Links */}
                    <div className="hidden md:flex items-center space-x-8">
                        <Link to="/rentals" className="text-gray-600 hover:text-blue-600 transition-colors">
                            Browse Rentals
                        </Link>

                        {isAuthenticated && user?.user_type === 'landlord' && (
                            <Link
                                to="/rentals/create"
                                className="flex items-center space-x-1 text-gray-600 hover:text-blue-600 transition-colors"
                            >
                                <PlusIcon className="h-4 w-4" />
                                <span>Add Property</span>
                            </Link>
                        )}
                    </div>

                    {/* User Menu */}
                    <div className="flex items-center space-x-4">
                        {isAuthenticated ? (
                            <>
                                {user?.user_type === 'tenant' && (
                                    <Link
                                        to="/favorites"
                                        className="p-2 text-gray-600 hover:text-blue-600 transition-colors"
                                    >
                                        <HeartIcon className="h-5 w-5" />
                                    </Link>
                                )}

                                <Link
                                    to="/dashboard"
                                    className="p-2 text-gray-600 hover:text-blue-600 transition-colors"
                                >
                                    <UserIcon className="h-5 w-5" />
                                </Link>

                                <button
                                    onClick={handleLogout}
                                    className="btn btn-outline"
                                >
                                    Logout
                                </button>
                            </>
                        ) : (
                            <div className="flex items-center space-x-4">
                                <Link to="/login" className="text-gray-600 hover:text-blue-600 transition-colors">
                                    Login
                                </Link>
                                <Link to="/register" className="btn btn-primary">
                                    Sign Up
                                </Link>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </nav>
    )
}

export default Navbar